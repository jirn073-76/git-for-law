"""NOR XML-based law parser for git-for-law Austria.

Instead of scraping RIS GeltendeFassung HTML, this module:
1. Queries OGD API v2.6 for NOR references at a given Fassung date
2. Fetches structured NOR XML (https://www.ris.bka.gv.at/Dokumente/Bundesnormen/NOR{id}/NOR{id}.xml)
3. Parses XML into the same section format the pipeline expects

Caching: NOR XML cached to nor_cache/<nor_id>.xml, index cached to
nor_cache/<gsn>_<fassung_vom>.json.
"""

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import requests

API_URL = "https://data.bka.gv.at/ris/api/v2.6/Bundesrecht"
XML_URL = "https://www.ris.bka.gv.at/Dokumente/Bundesnormen"
NAMESPACE = "http://www.bka.gv.at"

_API_SEMAPHORE = threading.Semaphore(5)

METADATA_CT = {
    "kurztitel", "kundmachungsorgan", "typ", "artikel_anlage",
    "ikra", "auki", "aenderung", "abkuerzung", "indizes",
    "doktyp", "erl", "stammnorm", "uebergangsrecht",
    "index", "langtitel", "umsetzungshinweis",
}


class NORCache:
    """File-system cache for NOR index and XML content."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, filename: str) -> Path:
        return self.cache_dir / filename

    def get(self, filename: str) -> Optional[str]:
        p = self._path(filename)
        if p.exists():
            return p.read_text(encoding="utf-8")
        return None

    def put(self, filename: str, content: str) -> None:
        self._path(filename).write_text(content, encoding="utf-8")

    def get_json(self, filename: str) -> Optional[dict]:
        raw = self.get(filename)
        return json.loads(raw) if raw else None

    def put_json(self, filename: str, data: dict) -> None:
        self.put(filename, json.dumps(data, ensure_ascii=False, indent=2))


def _rate_limited_request(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    """Rate-limit to 200ms between requests with retry on DNS/connection errors."""
    time.sleep(0.2)
    delay = 1.0
    for attempt in range(4):
        try:
            with _API_SEMAPHORE:
                resp = session.request(method, url, timeout=(15, 60), **kwargs)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 3:
                raise
            time.sleep(delay * (2 ** attempt))
    raise RuntimeError("unreachable")


def fetch_nor_index(gsn: str, fassung_vom: str, cache: NORCache, session: requests.Session) -> list[dict]:
    """Fetch all NOR references for a law at a given Fassung date.

    Returns list of {nor_id, apa, typ, inkrafttretensdatum, titel}.
    """
    cache_key = f"{gsn}_{fassung_vom}.json"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached

    results = []
    page = 1
    while True:
        body = (
            f"Applikation=BrKons"
            f"&Gesetzesnummer={gsn}"
            f"&Fassung.FassungVom={fassung_vom}"
            f"&Seitennummer={page}"
            f"&DokumenteProSeite=OneHundred"
        )
        resp = _rate_limited_request(
            session, "POST", API_URL,
            data=body.encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
        data = resp.json()
        try:
            refs = data["OgdSearchResult"]["OgdDocumentResults"]["OgdDocumentReference"]
        except KeyError:
            break

        for ref in refs:
            meta = ref["Data"]["Metadaten"]
            br = meta.get("Bundesrecht", {}).get("BrKons", {})
            results.append({
                "nor_id": meta["Technisch"]["ID"],
                "apa": br.get("ArtikelParagraphAnlage", ""),
                "typ": br.get("Typ", ""),
                "inkrafttretensdatum": br.get("Inkrafttretensdatum", ""),
                "titel": br.get("Titel", ""),
            })

        hits = data["OgdSearchResult"]["OgdDocumentResults"]["Hits"]
        total = int(hits.get("#text", 0))
        page_size = int(hits.get("@pageSize", 100))
        if page * page_size >= total:
            break
        page += 1

    cache.put_json(cache_key, results)
    return results


def fetch_nor_xml(nor_id: str, cache: NORCache, session: requests.Session) -> str:
    """Fetch NOR XML content, with caching."""
    cache_key = f"{nor_id}.xml"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    url = f"{XML_URL}/{nor_id}/{nor_id}.xml"
    resp = _rate_limited_request(session, "GET", url)
    text = resp.text
    cache.put(cache_key, text)
    return text


def strip_ns(tag: str) -> str:
    """Strip XML namespace from tag."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def parse_nor_xml(xml_text: str, nor_id: str, apa: str = "") -> dict:
    """Parse a single NOR XML document into a section dict.

    Returns {"section_id": ..., "heading": ..., "body": ..., "body_blocks": ..., "section_type": ...}
    or {} if parsing fails.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}

    nutzdaten = root.find(f"{{{NAMESPACE}}}nutzdaten")
    if nutzdaten is None:
        return {}

    heading_parts = []
    blocks = []
    gldsym_text = ""

    def _text(elem) -> str:
        return "".join(elem.itertext()).strip()

    def _process_children(parent):
        nonlocal gldsym_text
        for child in parent:
            tag = strip_ns(child.tag)
            ct = child.get("ct", "")
            typ = child.get("typ", "")

            if tag == "ueberschrift":
                if typ == "titel":
                    continue
                if ct == "text":
                    t = _text(child)
                    if t:
                        heading_parts.append(t)

            elif tag == "absatz":
                if ct in METADATA_CT:
                    continue
                if ct == "text":
                    t = _text(child)
                    if t:
                        blocks.append({"type": "text", "text": t})
                        if not gldsym_text:
                            gld = child.find(f"{{{NAMESPACE}}}gldsym")
                            if gld is not None:
                                gldsym_text = "".join(gld.itertext()).strip()

            elif tag == "listelem":
                if ct == "text":
                    t = _text(child)
                    if t:
                        blocks.append({"type": "text", "text": t})

            elif tag == "schlussteil":
                if ct == "text":
                    t = _text(child)
                    if t:
                        blocks.append({"type": "text", "text": t})

            elif tag == "beschr":
                if ct == "text":
                    t = _text(child)
                    if t:
                        blocks.append({"type": "text", "text": t})

            elif tag in ("aufzaehlung", "ziffernliste", "literaliste",
                         "subliteraliste", "betragliste", "strichliste",
                         "erlliste", "betraglistetgue"):
                style = _list_style(tag, child)
                items = []
                for li in child:
                    if strip_ns(li.tag) == "listelem" and li.get("ct") == "text":
                        sym = li.find(f"{{{NAMESPACE}}}symbol")
                        if sym is not None:
                            sym_text = "".join(sym.itertext())
                            full = _text(li)
                            item_text = full.removeprefix(sym_text).strip()
                        else:
                            sym_text = ""
                            item_text = _text(li)
                        items.append({"symbol": sym_text, "text": item_text})
                if items:
                    blocks.append({"type": "list", "style": style, "items": items})

            elif tag == "liste":
                _process_list(child, blocks)

            elif tag == "abschnitt":
                _process_children(child)

            elif tag in ("kzinhalt", "fzinhalt", "abstand", "gldsym",
                         "symbol", "tab", "feld", "span",
                         "i", "b", "u", "sub", "link", "br", "gdash", "gs", "n",
                         "nbsp", "schluss", "super", "table", "td", "tr",
                         "binary", "src", "inhaltsvz", "bdash", "amp", "lt", "gt",
                         "pdeinst", "pdvorlage",
                         "aw", "en", "s"):
                pass

    _process_children(nutzdaten)

    if not blocks:
        return {}

    heading = " ".join(heading_parts)
    body = "\n".join(_render_block(b) for b in blocks)

    section_id = _derive_section_id(heading, body, gldsym_text, apa)
    section_type = _derive_section_type(heading, apa)

    return {
        "section_id": section_id,
        "heading": heading,
        "body": body,
        "body_blocks": blocks,
        "section_type": section_type,
    }


def _list_style(tag: str, list_elem) -> str:
    """Detect list style from symbols: 'ordered', 'letters', or 'roman'."""
    if tag in ("literaliste", "subliteraliste", "erlliste"):
        return "letters"
    if tag == "strichliste":
        return "dash"
    if tag == "ziffernliste":
        return "ordered"
    if tag == "betragliste":
        return "amounts"
    if tag == "betraglistetgue":
        return "letters"
    if tag == "aufzaehlung":
        first_li = list_elem.find(f"{{{NAMESPACE}}}listelem")
        if first_li is not None:
            sym = first_li.find(f"{{{NAMESPACE}}}symbol")
            if sym is not None:
                s = "".join(sym.itertext()).strip().lower().rstrip(".)")
                if re.match(r'^[a-z]+$', s):
                    return "letters"
                if re.match(r'^[ivxlcdm]+$', s):
                    return "roman"
    return "ordered"


def _process_list(list_elem, blocks):
    """Process a <liste> element into structured list blocks."""
    schluss_items = []
    for child in list_elem:
        tag = strip_ns(child.tag)
        if tag == "schlussteil" and child.get("ct") == "text":
            t = "".join(child.itertext()).strip()
            if t:
                schluss_items.append({"text": t})
        elif tag in ("aufzaehlung", "ziffernliste", "literaliste",
                     "subliteraliste", "betragliste", "strichliste",
                     "betraglistetgue"):
            if schluss_items:
                blocks.append({"type": "list", "style": "schluss", "items": schluss_items})
                schluss_items = []
            style = _list_style(tag, child)
            items = []
            for li in child:
                if strip_ns(li.tag) == "listelem" and li.get("ct") == "text":
                    sym = li.find(f"{{{NAMESPACE}}}symbol")
                    if sym is not None:
                        sym_text = "".join(sym.itertext())
                        full = "".join(li.itertext())
                        item_text = full.removeprefix(sym_text).strip()
                    else:
                        sym_text = ""
                        item_text = "".join(li.itertext()).strip()
                    items.append({"symbol": sym_text, "text": item_text})
            if items:
                blocks.append({"type": "list", "style": style, "items": items})
        elif tag == "liste":
            _process_list(child, blocks)
    if schluss_items:
        blocks.append({"type": "list", "style": "schluss", "items": schluss_items})


def _render_block(block: dict) -> str:
    """Render a single body block to plain text."""
    if block["type"] == "text":
        return block["text"]
    if block["type"] == "list":
        lines = []
        for item in block["items"]:
            sym = item.get("symbol", "")
            text = item.get("text", "")
            if sym:
                lines.append(f"{sym} {text}")
            else:
                lines.append(text)
        return "\n".join(lines)
    return ""


def _derive_section_id(heading: str, body: str, gldsym_text: str = "", apa: str = "") -> str:
    """Derive §_N, Art_N, Anlage_N from APA, gldsym, heading, or body text."""
    if apa:
        m = re.match(r'Art(?:ikel)?\.?\s*([IVXLCDM\d]+)\s*§\.?\s*(\d+[a-z]?)', apa, re.IGNORECASE)
        if m:
            return f"Art_{m.group(1)}_§_{m.group(2)}"
        m = re.match(r'Art(?:ikel)?\.?\s*([IVXLCDM\d]+)', apa, re.IGNORECASE)
        if m:
            return f"Art_{m.group(1)}"
        m = re.match(r'§\.?\s*(\d+[a-z]?)', apa, re.IGNORECASE)
        if m:
            return f"§_{m.group(1)}"
    if gldsym_text:
        m = re.match(r'(?:Art(?:ikel)?\.?\s*)?§\.?\s*(\d+[a-z]?)\b', gldsym_text, re.IGNORECASE)
        if m:
            return f"§_{m.group(1)}"
        m = re.match(r'Art(?:ikel)?\.?\s*([IVXLCDM]+|\d+)\b', gldsym_text, re.IGNORECASE)
        if m:
            return f"Art_{m.group(1)}"
    if heading:
        m = re.match(r'(?:Art(?:ikel)?\.?\s*)?§\.?\s*(\d+[a-z]?)\b', heading, re.IGNORECASE)
        if m:
            return f"§_{m.group(1)}"
        m = re.match(r'Art(?:ikel)?\.?\s*([IVXLCDM]+|\d+)\b', heading, re.IGNORECASE)
        if m:
            return f"Art_{m.group(1)}"
        m = re.match(r'Anlage\s+(\d+[a-z]?)', heading, re.IGNORECASE)
        if m:
            return f"Anlage_{m.group(1)}"
    if body:
        m = re.match(r'(?:Art(?:ikel)?\.?\s*)?§\.?\s*(\d+[a-z]?)\.?\s', body, re.IGNORECASE)
        if m:
            return f"§_{m.group(1)}"
        m = re.match(r'Art(?:ikel)?\.?\s*([IVXLCDM]+|\d+)', body, re.IGNORECASE)
        if m:
            return f"Art_{m.group(1)}"
    return f"Section-{hash(body) % 10000}"


def _derive_section_type(heading: str, apa: str = "") -> str:
    if apa:
        if "§" in apa:
            return "Paragraf"
        if re.match(r'Art(?:ikel)?', apa, re.IGNORECASE):
            return "Artikel"
        if "Anlage" in apa:
            return "Anlage"
    if re.match(r'Art(?:ikel)?\.?\s*[IVXLCDM\d]', heading, re.IGNORECASE):
        return "Artikel"
    if heading.startswith("§"):
        return "Paragraf"
    if "Anlage" in heading:
        return "Anlage"
    return "Paragraf"


def build_fassung(gsn: str, fassung_vom: str, cache: NORCache, session: requests.Session) -> dict:
    """Build a complete fassung.json dict for a law at a given date from NOR XML."""
    nor_refs = fetch_nor_index(gsn, fassung_vom, cache, session)

    sections = {}
    for ref in nor_refs:
        nor_id = ref["nor_id"]
        try:
            xml_text = fetch_nor_xml(nor_id, cache, session)
        except requests.RequestException:
            continue

        parsed = parse_nor_xml(xml_text, nor_id, ref.get("apa", ""))
        if not parsed or not parsed.get("body"):
            continue

        sid = parsed["section_id"]
        if sid not in sections or len(parsed["body"]) > len(sections[sid].get("body", "")):
            sections[sid] = {
                "section_id": sid,
                "heading": parsed["heading"],
                "body": parsed["body"],
                "body_blocks": parsed.get("body_blocks", []),
                "section_type": parsed["section_type"],
            }

    return sections

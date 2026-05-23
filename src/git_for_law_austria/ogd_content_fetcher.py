import html as _html
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests


@dataclass
class NORContent:
    nor_id: str = ""
    heading: str = ""
    body: str = ""
    section_type: str = "Paragraf"
    section_number: str = ""
    metadata: dict = field(default_factory=dict)


class OGDContentFetcher:
    OGD_SEARCH_URL = "https://data.bka.gv.at/ris/api/v2.6/Bundesrecht"
    DOKUMENT_BASE_URL = "https://www.ris.bka.gv.at/Dokumente/Bundesnormen"
    USER_AGENT = "GitForLaw/1.0"

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        rate_limit: float = 0.3,
        timeout: float = 30,
    ):
        self._session = session or requests.Session()
        self._session.headers.setdefault("User-Agent", self.USER_AGENT)
        self.min_interval = rate_limit
        self._timeout = timeout
        self._last_request_time: float = 0.0

    def _build_dokument_url(self, nor_id: str) -> str:
        return f"{self.DOKUMENT_BASE_URL}/{nor_id}/{nor_id}.xml"

    def _raw_http_get(self, url: str) -> requests.Response:
        return self._session.get(url, timeout=self._timeout)

    def _http_get(self, url: str) -> requests.Response:
        return self._session.get(url, timeout=self._timeout)

    def _http_post(
        self, url: str, data: Optional[dict] = None
    ) -> requests.Response:
        return self._session.post(url, data=data, timeout=self._timeout)

    def _rate_limited_get(self, url: str) -> requests.Response:
        now = time.monotonic()
        if self._last_request_time > 0:
            elapsed = now - self._last_request_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
        response = self._raw_http_get(url)
        self._last_request_time = time.monotonic()
        return response

    _PER_PAGE_VALUES = {20: "Twenty", 50: "Fifty", 100: "OneHundred"}

    def _search_ogd(
        self,
        gsn: str,
        fassung_vom: str = "",
        FassungVom: str = "",
        page: int = 1,
        per_page: int = 100,
    ) -> dict:
        fassung = FassungVom or fassung_vom
        per_page_text = self._PER_PAGE_VALUES.get(per_page, "OneHundred")
        data = {
            "Applikation": "BrKons",
            "Gesetzesnummer": gsn,
            "FassungVom": fassung,
            "Seitennummer": str(page),
            "DokumenteProSeite": per_page_text,
        }
        response = self._http_post(self.OGD_SEARCH_URL, data=data)
        return response.json()

    def _extract_refs(self, search_response: dict) -> list:
        return (
            search_response.get("OgdSearchResult", {})
            .get("OgdDocumentResults", {})
            .get("OgdDocumentReference", [])
        )

    def _extract_nor_id(self, ref: dict) -> str:
        # Primary path: Data.Metadaten.Technisch.ID
        try:
            return ref["Data"]["Metadaten"]["Technisch"]["ID"]
        except (KeyError, TypeError):
            pass
        # Fallback: extract NOR ID from ContentReference URL
        content_ref = ref.get("ContentReference", "")
        if content_ref:
            m = re.search(r"/NOR(\d+)/", content_ref)
            if m:
                return f"NOR{m.group(1)}"
        return ""

    def _extract_nor_ids(self, search_response: dict) -> list:
        nor_ids: list = []
        seen: set = set()
        for ref in self._extract_refs(search_response):
            nor_id = self._extract_nor_id(ref)
            if nor_id and nor_id not in seen:
                nor_ids.append(nor_id)
                seen.add(nor_id)
        return nor_ids

    def _is_stammnorm(self, section_id: str) -> bool:
        return section_id in ("§ 0", "§_0")

    def _detect_section_type(self, heading: str) -> str:
        if heading.startswith("Art."):
            return "Artikel"
        if heading.startswith("Anlage"):
            return "Anlage"
        return "Paragraf"

    _META_UEBERSCHRIFT = {
        "Kurztitel", "Kundmachungsorgan", "§/Artikel/Anlage",
        "Inkrafttretensdatum", "Text",
    }

    def _strip_xml_tags(self, text: str) -> str:
        """Remove XML tags (tab, feld, etc.) from extracted text."""
        text = re.sub(r"<[^>]+>", "", text)
        text = _html.unescape(text.strip())
        return re.sub(r"\s+", " ", text).strip()

    def _is_meta_absatz(self, text: str, heading: str) -> bool:
        """Return True if the absatz text is metadata, not legal content."""
        if re.match(r"^Bundesrecht\s+konsolidiert$", text):
            return True
        if re.match(r"^www\.ris\.bka\.gv\.at", text):
            return True
        if re.match(r"^Seite\s+\d+\s+von\s+\d+$", text):
            return True
        if re.match(r"^\d{2}\.\d{2}\.\d{4}$", text):
            return True
        if re.match(r"^(JGS|RGBl|BGBl|StGBl|dRGBl|StF)", text):
            return True
        # Filter out the section heading duplicate
        norm_heading = heading.strip().rstrip(".")
        norm_text = text.strip().rstrip(".")
        if norm_heading == norm_text:
            return True
        return False

    def _parse_dokument_xml(
        self, xml_text: str, nor_id: Optional[str] = None
    ) -> NORContent:
        gldsym_match = re.search(
            r"<gldsym>(.*?)</gldsym>", xml_text, re.DOTALL
        )
        heading = ""
        if gldsym_match:
            heading = self._strip_xml_tags(gldsym_match.group(1))

        if self._is_stammnorm(heading):
            return NORContent(
                nor_id=nor_id or "",
                heading=heading,
                body="",
                section_type=self._detect_section_type(heading),
            )

        elements: list = []

        # ueberschrift — filter meta labels
        for m in re.finditer(
            r"<ueberschrift[^>]*>(.*?)</ueberschrift>", xml_text, re.DOTALL
        ):
            text = self._strip_xml_tags(m.group(1))
            if text and text not in self._META_UEBERSCHRIFT:
                elements.append(text)

        # absatz — filter metadata patterns
        for m in re.finditer(
            r"<absatz[^>]*>(.*?)</absatz>", xml_text, re.DOTALL
        ):
            text = self._strip_xml_tags(m.group(1))
            if text and not self._is_meta_absatz(text, heading):
                elements.append(text)

        body = "\n".join(elements)
        section_type = self._detect_section_type(heading)

        return NORContent(
            nor_id=nor_id or "",
            heading=heading,
            body=body,
            section_type=section_type,
        )

    def _fetch_dokument(self, nor_id: str) -> NORContent:
        url = self._build_dokument_url(nor_id)
        try:
            response = self._http_get(url)
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ):
            return NORContent(nor_id=nor_id)
        if response.status_code != 200:
            return NORContent(nor_id=nor_id)
        try:
            return self._parse_dokument_xml(response.text, nor_id=nor_id)
        except Exception:
            return NORContent(nor_id=nor_id)

    def _fetch_dokument_with_meta(
        self, nor_id: str, section_number: str, apa: str
    ) -> NORContent:
        content = self._fetch_dokument(nor_id)
        content.section_number = section_number
        if not content.heading and apa:
            content.heading = apa
        if not content.section_type or content.section_type == "Paragraf":
            if apa.startswith("Art."):
                content.section_type = "Artikel"
            elif apa.startswith("Anlage"):
                content.section_type = "Anlage"
        return content

    def _fetch_multiple_dokumente(self, nor_ids: list) -> list:
        return [self._fetch_dokument(nid) for nid in nor_ids]

    def _get_total_hits(self, search_response: dict) -> int:
        try:
            hits = (
                search_response.get("OgdSearchResult", {})
                .get("OgdDocumentResults", {})
                .get("Hits", {})
            )
            return int(hits.get("#text", 0))
        except (TypeError, ValueError):
            return 0

    def fetch_law_content(
        self,
        gsn: str,
        fassung_vom: str,
        max_sections: Optional[int] = None,
    ) -> list:
        page = 1
        per_page = 100
        seen_nor_ids: set = set()
        results: list = []

        while True:
            search_response = self._search_ogd(
                gsn, FassungVom=fassung_vom, page=page, per_page=per_page
            )
            refs = self._extract_refs(search_response)
            if not refs:
                break

            for ref in refs:
                nor_id = self._extract_nor_id(ref)
                if not nor_id or nor_id in seen_nor_ids:
                    continue
                seen_nor_ids.add(nor_id)

                try:
                    brkons = (
                        ref.get("Data", {})
                        .get("Metadaten", {})
                        .get("Bundesrecht", {})
                        .get("BrKons", {})
                    )
                except (KeyError, TypeError):
                    brkons = {}
                apa = brkons.get("ArtikelParagraphAnlage", "")
                para_num = brkons.get("Paragraphnummer", "")

                if self._is_stammnorm(apa):
                    continue

                content = self._fetch_dokument_with_meta(nor_id, para_num, apa)
                if content.body:
                    results.append(content)

                if max_sections is not None and len(results) >= max_sections:
                    break

            if max_sections is not None and len(results) >= max_sections:
                break

            total = self._get_total_hits(search_response)
            if page * per_page >= total:
                break
            page += 1

        return results

    def build_dokument_url(self, nor_id: str) -> str:
        return self._build_dokument_url(nor_id)

    def extract_nor_ids(self, search_response: dict) -> list:
        refs = self._extract_refs(search_response)
        result = []
        for ref in refs:
            nor_id = self._extract_nor_id(ref)
            try:
                brkons = (
                    ref.get("Data", {})
                    .get("Metadaten", {})
                    .get("Bundesrecht", {})
                    .get("BrKons", {})
                )
            except (KeyError, TypeError):
                brkons = {}
            apa = brkons.get("ArtikelParagraphAnlage", "")
            para_num = brkons.get("Paragraphnummer", "")
            section_type = "Paragraf"
            if apa.startswith("Art."):
                section_type = "Artikel"
            elif apa.startswith("Anlage"):
                section_type = "Anlage"
            result.append({
                "nor_id": nor_id,
                "section_type": section_type,
                "section_number": para_num,
                "apa": apa,
            })
        return result

    def parse_dokument_xml(self, xml_text: str) -> NORContent:
        return self._parse_dokument_xml(xml_text)

    def fetch_section_text(self, nor_id: str) -> str:
        content = self._fetch_dokument(nor_id)
        return content.body

    def fetch_all_sections(
        self,
        search_response: dict,
        max_sections: Optional[int] = None,
    ) -> list:
        nor_ids = self._extract_nor_ids(search_response)
        if max_sections is not None:
            nor_ids = nor_ids[:max_sections]
        results = []
        for nor_id in nor_ids:
            url = self._build_dokument_url(nor_id)
            try:
                response = self._rate_limited_get(url)
                if response.status_code == 200:
                    content = self._parse_dokument_xml(
                        response.text, nor_id=nor_id
                    )
                else:
                    content = NORContent(nor_id=nor_id)
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ):
                content = NORContent(nor_id=nor_id)
            results.append(content)
        return results

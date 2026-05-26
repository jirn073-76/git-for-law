#!/usr/bin/env python3
"""Parallel NOR XML pipeline — processes all laws through structured NOR XML.

Replaces the HTML GeltendeFassung parser with structured NOR XML parsing.
Each worker rate-limits to 200ms between API calls. Checkpoint-based resume
so interrupted runs pick up where they left off.

Usage:
    python scripts/nor_batch_pipeline.py                    # process all pending
    python scripts/nor_batch_pipeline.py --workers 5        # custom worker count
    python scripts/nor_batch_pipeline.py --only ABGB        # single law
    python scripts/nor_batch_pipeline.py --reset            # clear checkpoint, start fresh
"""

import json
import os
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from git_for_law_austria.nor_xml import (
    NORCache,
    fetch_nor_index,
    fetch_nor_xml,
    parse_nor_xml,
    NAMESPACE,
    strip_ns,
)

CACHE_DIR = REPO_ROOT / "data" / "nor_cache"
LAWS_DIR = REPO_ROOT / "data" / "laws"
GSN_MAP_PATH = REPO_ROOT / "data" / "gsn_to_abbrev.json"
CHECKPOINT_PATH = REPO_ROOT / "data" / "nor_checkpoint.json"

API_URL = "https://data.bka.gv.at/ris/api/v2.6/Bundesrecht"

_unknown_tags = set()
_tags_lock = threading.Lock()

_session_local = threading.local()


def _get_session():
    if not hasattr(_session_local, "session"):
        s = requests.Session()
        s.headers.update({"User-Agent": "git-for-law/0.1 (research; d.ramadani@ieee.org)"})
        _session_local.session = s
    return _session_local.session


def log_unknown_tag(tag: str):
    with _tags_lock:
        _unknown_tags.add(tag)


def _discover_tags_in_xml(xml_text: str) -> set:
    """Scan an XML document for tags not handled by the parser."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return set()
    unknown = set()
    handled = {
        "absatz", "abschnitt", "abstand", "amp", "aufzaehlung", "b", "bdash",
        "betragliste", "betraglistetgue", "binary", "br", "feld", "fzinhalt", "gdash", "gldsym", "gs",
        "i", "inhaltsvz", "kzinhalt", "liste", "listelem", "literaliste", "link",
        "lt", "metadaten", "n", "nbsp", "nutzdaten", "pdeinst", "pdvorlage",
        "risdok", "s", "schluss", "schlussteil", "span", "src", "strichliste",
        "beschr", "aw", "en",
        "sub", "subliteraliste", "super", "symbol", "tab", "table", "td", "tr", "u",
        "ueberschrift", "ziffernliste", "layoutdaten", "erl", "erlliste", "gt",
    }
    for elem in root.iter():
        tag = elem.tag.split("}", 1)[1] if "}" in elem.tag else elem.tag
        if tag not in handled:
            unknown.add(tag)
    return unknown


def _request_with_retry(session, method, url, max_retries=4, **kwargs):
    """Retry with exponential backoff on DNS/connection errors."""
    from git_for_law_austria.nor_xml import _API_SEMAPHORE

    delay = 1.0
    for attempt in range(max_retries + 1):
        try:
            with _API_SEMAPHORE:
                resp = session.request(method, url, timeout=(15, 60), **kwargs)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout):
            if attempt == max_retries:
                raise
            time.sleep(delay * (2 ** attempt))
    return None


def fetch_ogd_metadata(gsn: str, max_pages: int = 30) -> list:
    """Fetch all Fassung metadata for a GSN from the OGD API.

    Returns list of dicts with keys: fassung_vom, aenderung, ris_url.
    """
    items = []
    session = _get_session()
    page = 1
    while page <= max_pages:
        body = (
            f"Applikation=BrKons"
            f"&Gesetzesnummer={gsn}"
            f"&Seitennummer={page}"
            f"&DokumenteProSeite=OneHundred"
        )
        try:
            resp = _request_with_retry(
                session, "POST", API_URL,
                data=body.encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            )
        except (requests.ConnectionError, requests.Timeout):
            break

        data = resp.json()

        try:
            refs = data["OgdSearchResult"]["OgdDocumentResults"]["OgdDocumentReference"]
        except KeyError:
            break

        for ref in refs:
            if not isinstance(ref, dict):
                continue
            meta = ref.get("Data", {}).get("Metadaten", {})
            br = meta.get("Bundesrecht", {}).get("BrKons", {})
            fassung_vom = br.get("Inkrafttretensdatum", "")
            if fassung_vom:
                items.append({
                    "fassung_vom": fassung_vom,
                    "aenderung": br.get("Kundmachungsorgan", ""),
                    "ris_url": ref.get("ContentReference", ""),
                })

        hits = data["OgdSearchResult"]["OgdDocumentResults"]["Hits"]
        total = int(hits.get("#text", 0))
        page_size = int(hits.get("@pageSize", 100))
        if page * page_size >= total:
            break
        page += 1

    return items


def _group_by_date(items: list) -> dict:
    grouped = {}
    for item in items:
        date_key = item["fassung_vom"]
        if date_key not in grouped:
            grouped[date_key] = item
    return grouped


def process_law(gsn: str, abbrev: str) -> dict:
    """Process a single law: fetch all Fassungen via NOR XML, commit to git.

    Returns {"abbrev": ..., "gsn": ..., "versions": ..., "sections": ..., "errors": ...}
    """
    t_start = time.monotonic()
    result = {"abbrev": abbrev, "gsn": gsn, "versions": 0, "sections": 0, "errors": []}

    session = _get_session()
    cache = NORCache(CACHE_DIR)

    items = fetch_ogd_metadata(gsn)
    if not items:
        result["errors"].append("No metadata")
        return result

    by_date = _group_by_date(items)
    dates = sorted(by_date.keys())
    fassungen = {}

    for date in dates:
        try:
            nor_refs = fetch_nor_index(gsn, date, cache, session)
        except Exception as e:
            result["errors"].append(f"Index {date}: {e}")
            continue

        sections = {}
        for ref in nor_refs:
            nor_id = ref["nor_id"]
            try:
                xml_text = fetch_nor_xml(nor_id, cache, session)
            except Exception as e:
                result["errors"].append(f"XML {nor_id}: {e}")
                continue

            unknown = _discover_tags_in_xml(xml_text)
            for tag in unknown:
                log_unknown_tag(tag)

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
                    "fassung_vom": date,
                }

        if sections:
            fassungen[date] = sections
            result["sections"] = max(result["sections"], len(sections))

    if not fassungen:
        result["errors"].append("No sections built")
        return result

    result["versions"] = len(fassungen)

    # Commit to git
    law_dir = LAWS_DIR / abbrev
    law_dir.mkdir(parents=True, exist_ok=True)

    git_dir = law_dir / ".git"
    if git_dir.exists():
        import shutil
        shutil.rmtree(str(git_dir))

    subprocess.run(
        ["git", "-C", str(law_dir), "init"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "-C", str(law_dir), "config", "user.name", "git-for-law"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(law_dir), "config", "user.email", "git-for-law@local"],
        check=True,
    )

    for date in sorted(fassungen.keys()):
        meta = by_date.get(date, {})
        aenderung = meta.get("aenderung", "")
        aenderung_clean = aenderung[:120] if len(aenderung) > 120 else aenderung

        (law_dir / "fassung.json").write_text(
            json.dumps(fassungen[date], ensure_ascii=False, indent=2) + "\n"
        )
        subprocess.run(["git", "-C", str(law_dir), "add", "fassung.json"], check=True)

        env = {}
        if "1970-01-01" <= date < "2100-01-01":
            env["GIT_AUTHOR_DATE"] = f"{date} 12:00:00 +0000"
            env["GIT_COMMITTER_DATE"] = f"{date} 12:00:00 +0000"
        try:
            subprocess.run(
                ["git", "-C", str(law_dir), "commit", "-m",
                 f"{abbrev} [{date}]: {aenderung_clean}"],
                env={**os.environ, **env},
                check=True,
            )
        except subprocess.CalledProcessError as e:
            result["errors"].append(f"Commit {date}: {e}")

    elapsed = time.monotonic() - t_start
    status = "OK" if not result["errors"] else f"ERR: {'; '.join(result['errors'][:2])}"
    print(f"  {abbrev:30s}  {result['versions']:4d} versions  "
          f"{result['sections']:5d} sections  {elapsed:6.1f}s  {status}", flush=True)
    return result


def load_checkpoint() -> set:
    if CHECKPOINT_PATH.exists():
        data = json.loads(CHECKPOINT_PATH.read_text())
        return set(data.get("done", []))
    return set()


def save_checkpoint(done: set):
    CHECKPOINT_PATH.write_text(
        json.dumps({"done": sorted(done), "updated": time.strftime("%Y-%m-%dT%H:%M:%S")},
                   ensure_ascii=False, indent=2)
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="NOR XML batch pipeline")
    parser.add_argument("--workers", type=int, default=7, help="Number of worker threads (default: 7)")
    parser.add_argument("--only", type=str, help="Process a single law by abbreviation")
    parser.add_argument("--reset", action="store_true", help="Clear checkpoint and start fresh")
    args = parser.parse_args()

    if not GSN_MAP_PATH.exists():
        print(f"ERROR: GSN map not found at {GSN_MAP_PATH}")
        print("Run scripts/scan_all_gsns.py and scripts/match_law_index.py first.")
        sys.exit(1)

    with open(GSN_MAP_PATH) as f:
        gsn_map = json.load(f)

    if args.reset:
        if CHECKPOINT_PATH.exists():
            CHECKPOINT_PATH.unlink()
        print("Checkpoint reset.")

    if args.only:
        abbrev = args.only
        abbrev_to_gsn = {v: k for k, v in gsn_map.items()}
        gsn = abbrev_to_gsn.get(abbrev)
        if not gsn:
            print(f"ERROR: '{abbrev}' not found in GSN map")
            sys.exit(1)
        result = process_law(gsn, abbrev)
        print(f"\nDone: {result['versions']} versions, {result['sections']} sections")
        if result["errors"]:
            for e in result["errors"]:
                print(f"  ERR: {e}")
        return

    done = load_checkpoint()
    pending = [gsn for gsn in sorted(gsn_map.keys()) if gsn not in done]

    print(f"Laws: {len(gsn_map)} total, {len(done)} done, {len(pending)} pending")
    print(f"Workers: {args.workers}")
    print(f"Cache dir: {CACHE_DIR}")
    print(f"Laws dir: {LAWS_DIR}")
    print()

    total_versions = 0
    total_sections = 0
    errors_list = []
    count_lock = threading.Lock()

    def run_one(gsn):
        abbrev = gsn_map[gsn]
        return process_law(gsn, abbrev)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, gsn): gsn for gsn in pending}

        for future in as_completed(futures):
            gsn = futures[future]
            try:
                result = future.result()
            except Exception as e:
                abbrev = gsn_map.get(gsn, gsn)
                print(f"  {abbrev} FAILED: {e}", flush=True)
                errors_list.append(f"{gsn}: {e}")
                result = {"abbrev": abbrev, "gsn": gsn, "versions": 0, "sections": 0, "errors": [str(e)]}

            with count_lock:
                total_versions += result["versions"]
                total_sections += result["sections"]
                if result["errors"]:
                    errors_list.append(f"{result['abbrev']}: {'; '.join(result['errors'])}")

            done.add(gsn)
            if len(done) % 10 == 0:
                save_checkpoint(done)

    save_checkpoint(done)

    print(f"\n{'='*60}")
    print(f"Done: {len(done)} laws, {total_versions} versions, {total_sections} sections")
    if errors_list:
        print(f"Errors ({len(errors_list)}):")
        for e in errors_list[:20]:
            print(f"  {e}")


if __name__ == "__main__":
    main()

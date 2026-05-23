"""Complete OGD API scanner — exhaustively extract all GSNs by type."""

import json
import sys
import time
from pathlib import Path

import requests

API_URL = "https://data.bka.gv.at/ris/api/v2.6/Bundesrecht"
RATE = 0.3


def fetch_page(typ, page, per_page="OneHundred"):
    """Fetch one page of OGD API results for a given Typ."""
    body = (
        f"Applikation=BrKons"
        f"&Typ={typ}"
        f"&Seitennummer={page}"
        f"&DokumenteProSeite={per_page}"
    )
    resp = requests.post(
        API_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        hits = int(
            data["OgdSearchResult"]["OgdDocumentResults"]["Hits"]["#text"]
        )
    except (KeyError, TypeError, ValueError):
        hits = 0
    refs = data["OgdSearchResult"]["OgdDocumentResults"]["OgdDocumentReference"]
    return hits, refs


def extract_gsns(refs):
    """Extract unique GSN metadata from document references."""
    found = {}
    for ref in refs:
        try:
            meta = ref["Data"]["Metadaten"]["Bundesrecht"]
            brkons = meta.get("BrKons", {})
            gsn = brkons.get("Gesetzesnummer", "")
            if not gsn:
                continue
            if gsn in found:
                continue
            found[gsn] = {
                "name": meta.get("Kurztitel", ""),
                "abbrev": brkons.get("Abkuerzung", ""),
                "typ": brkons.get("Typ", ""),
            }
        except (KeyError, TypeError):
            continue
    return found


def scan_type(typ, total_pages):
    """Scan all pages of a given type, extracting unique GSNs."""
    all_gsns = {}
    last_new = 0
    for page in range(1, total_pages + 1):
        time.sleep(RATE)
        try:
            hits, refs = fetch_page(typ, page)
        except Exception as e:
            print(f"  page {page}: ERROR {e}", flush=True)
            continue

        new = extract_gsns(refs)
        new_count = len([g for g in new if g not in all_gsns])
        all_gsns.update(new)

        if new_count > 0:
            last_new = page
        if page % 50 == 0 or new_count > 0:
            print(
                f"  {typ} page {page}/{total_pages}: +{new_count} new "
                f"(running total: {len(all_gsns)})",
                flush=True,
            )

        # Safety: if we've gone 200 pages without new GSNs, we're done
        if page - last_new > 200 and page > 500:
            print(f"  {typ}: no new GSNs for 200 pages, done at page {page}")
            break

    return all_gsns


def main():
    types_to_scan = [
        ("BG", 2100),    # ~207,732 hits → ~2078 pages
        ("BVG", 500),    # estimate
    ]

    all_catalog = {}
    for typ, pages in types_to_scan:
        print(f"\nScanning Typ={typ} (up to {pages} pages)...", flush=True)
        gsns = scan_type(typ, pages)
        print(f"  {typ} done: {len(gsns)} unique GSNs", flush=True)
        all_catalog.update(gsns)

    # Save
    out = Path(__file__).resolve().parent.parent / "data" / "law_catalog_complete.json"
    out.write_text(json.dumps(all_catalog, ensure_ascii=False, indent=2))
    print(f"\nSaved {len(all_catalog)} GSNs to {out}")


if __name__ == "__main__":
    main()

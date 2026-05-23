#!/usr/bin/env python3
"""Comprehensive audit: compare every repo's HEAD sections against RIS Stammfassung.

Flags repos where:
  - RIS returns significantly more sections than we have (<75% coverage)
  - Sections are text-only (body < 50 chars)
  - RIS fetch fails (possible wrong abbreviation/GSN)
  - StF date cannot be determined

Usage: python3 scripts/comprehensive_audit.py --workers 8
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LAWS_DIR = DATA_DIR / "laws"
GSN_MAP_PATH = DATA_DIR / "gsn_to_abbrev.json"
AUDIT_OUTPUT = DATA_DIR / "comprehensive_audit.json"

RIS_FASSUNG_URL = (
    "https://www.ris.bka.gv.at/GeltendeFassung.wxe"
    "?Abfrage=Bundesnormen&Gesetzesnummer={gsn}&FassungVom={date}"
)
USER_AGENT = "GitForLaw/1.0 (audit)"
REQUEST_DELAY = 0.35

MY_PROJECT_SRC = "/mnt/c/Users/notyo/Documents/PhD/my-project/src"
sys.path.insert(0, MY_PROJECT_SRC)


def load_abbrev_to_gsn() -> dict[str, int]:
    with open(GSN_MAP_PATH) as f:
        gsn_to_abbrev = json.load(f)
    return {abbrev: int(gsn) for gsn, abbrev in gsn_to_abbrev.items()}


def find_stf_date(repo_path: Path) -> Optional[str]:
    try:
        output = subprocess.check_output(
            ["git", "-C", str(repo_path), "log", "--format=%H %s", "--reverse"],
            text=True, timeout=10,
        )
        for line in output.strip().split("\n"):
            if not line.strip():
                continue
            m = re.search(r"\[(\d{4}-\d{2}-\d{2})\]", line)
            if m and m.group(1) != "0000-00-00":
                return m.group(1)
            m = re.search(r"(\d{4}-\d{2}-\d{2})", line)
            if m and m.group(1) != "0000-00-00":
                return m.group(1)
    except Exception:
        pass
    return None


def fetch_ris_html(gsn: int, fassung_vom: str) -> Optional[str]:
    url = RIS_FASSUNG_URL.format(gsn=gsn, date=fassung_vom)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        if len(html) < 5000:
            return None
        return html
    except Exception:
        return None


def parse_ris_html(html: str) -> dict[str, dict[str, Any]]:
    from git_for_law_austria.ris_html_parser import parse_geltendefassung_html

    raw = parse_geltendefassung_html(html)
    sections: dict[str, dict[str, Any]] = {}
    for s in raw:
        sid = s.get("section_id", "").strip().rstrip(".")
        body = s.get("body", "").strip()
        if not sid or not body:
            continue
        sid = re.sub(r"^(§|Art|Artikel|Anlage)\s+", r"\1_", sid)
        sid = sid.replace(" ", "_")
        if sid not in sections:
            sections[sid] = {
                "section_id": sid,
                "heading": s.get("heading", ""),
                "body": body,
                "section_type": s.get("section_type", "Paragraf"),
            }
    return sections


def _normalize_sid(sid: str) -> str:
    s = sid.strip().lower().replace(" ", "_").replace(".", "")
    return re.sub(r"_+", "_", s)


def _extract_numbers(sid: str) -> str:
    nums = re.findall(r"\d+[a-z]?", sid.lower())
    return "_".join(nums) if nums else sid.lower()


def audit_one(args: tuple) -> dict[str, Any]:
    abbrev, gsn, laws_dir_str = args
    result = {
        "abbrev": abbrev, "gsn": gsn,
        "stf_date": None, "ris_sections": 0, "head_sections": 0,
        "missing": 0, "text_only": 0, "coverage_pct": 0,
        "status": "unknown", "error": None,
    }

    repo_path = Path(laws_dir_str) / abbrev
    if not repo_path.exists() or not (repo_path / ".git").exists():
        result["status"] = "no_repo"
        result["error"] = "Repo not found"
        return result

    fassung_path = repo_path / "fassung.json"
    if not fassung_path.exists():
        result["status"] = "no_fassung"
        result["error"] = "No fassung.json"
        return result

    # Find StF date
    stf_date = find_stf_date(repo_path)
    if not stf_date:
        result["status"] = "no_stf_date"
        result["error"] = "Could not determine StF date"
        return result
    result["stf_date"] = stf_date

    # Load HEAD sections
    with open(fassung_path) as f:
        head = json.load(f)
    result["head_sections"] = len(head)
    result["text_only"] = sum(1 for s in head.values() if len(s.get("body", "")) < 50)

    # Fetch RIS
    time.sleep(REQUEST_DELAY)
    html = fetch_ris_html(gsn, stf_date)
    if not html:
        # Try without date
        url = f"https://www.ris.bka.gv.at/GeltendeFassung.wxe?Abfrage=Bundesnormen&Gesetzesnummer={gsn}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        if not html or len(html) < 5000:
            result["status"] = "ris_fetch_failed"
            result["error"] = f"RIS fetch failed for StF {stf_date}"
            return result
        result["stf_date"] = "current"

    # Parse RIS
    ris_sections = parse_ris_html(html)
    result["ris_sections"] = len(ris_sections)
    if not ris_sections:
        result["status"] = "ris_empty"
        result["error"] = "No sections parsed from RIS"
        return result

    # Build lookup
    head_norm = {_normalize_sid(sid): sid for sid in head}
    head_nums = {}
    for sid in head:
        num = _extract_numbers(sid)
        if num not in head_nums:
            head_nums[num] = sid

    # Count missing
    missing = 0
    for ris_sid in ris_sections:
        nsid = _normalize_sid(ris_sid)
        num = _extract_numbers(ris_sid)
        if nsid in head_norm:
            continue
        if num in head_nums:
            continue
        missing += 1

    result["missing"] = missing
    expected = len(ris_sections)
    result["coverage_pct"] = round(100 * (expected - missing) / max(expected, 1), 1)

    # Classify
    if result["coverage_pct"] >= 95 and result["text_only"] == 0:
        result["status"] = "complete"
    elif result["coverage_pct"] >= 80 and result["text_only"] <= 3:
        result["status"] = "minor_gaps"
    elif result["coverage_pct"] >= 50:
        result["status"] = "significant_gaps"
    else:
        result["status"] = "severe_gaps"

    if result["text_only"] > 5:
        result["status"] = "text_only_heavy"

    return result


def main():
    parser = argparse.ArgumentParser(description="Comprehensive RIS-backed audit of all laws")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--law", type=str)
    args = parser.parse_args()

    abbrev_to_gsn = load_abbrev_to_gsn()
    print(f"Loaded {len(abbrev_to_gsn)} GSN mappings")

    # Build work list
    work_items = []
    skipped_no_gsn = []

    if args.law:
        gsn = abbrev_to_gsn.get(args.law)
        if gsn:
            work_items.append((args.law, gsn, str(LAWS_DIR)))
        else:
            print(f"ERROR: No GSN for {args.law}")
            sys.exit(1)
    else:
        for d in sorted(LAWS_DIR.iterdir()):
            if not d.is_dir() or not (d / ".git").exists():
                continue
            abbrev = d.name
            gsn = abbrev_to_gsn.get(abbrev)
            if gsn:
                work_items.append((abbrev, gsn, str(LAWS_DIR)))
            else:
                skipped_no_gsn.append(abbrev)

    if args.limit > 0:
        work_items = work_items[:args.limit]

    print(f"Work items: {len(work_items)} (skipped no-GSN: {len(skipped_no_gsn)})")
    if args.law:
        result = audit_one(work_items[0])
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Run audit
    results = []
    total = len(work_items)

    with Pool(processes=args.workers) as pool:
        for i, r in enumerate(pool.imap_unordered(audit_one, work_items)):
            results.append(r)
            status_icon = {"complete": "✓", "minor_gaps": "~", "significant_gaps": "⚠",
                           "severe_gaps": "✗", "text_only_heavy": "T", "ris_fetch_failed": "?",
                           "no_stf_date": "D", "ris_empty": "E", "unknown": "?"}.get(r["status"], "?")
            print(f"[{i+1}/{total}] {status_icon} {r['abbrev']:<20} "
                  f"RIS:{r.get('ris_sections',0):>4} HEAD:{r.get('head_sections',0):>5} "
                  f"missing:{r.get('missing',0):>4} cov:{r.get('coverage_pct',0):>5}% "
                  f"text-only:{r.get('text_only',0)} {r.get('status','')}")

    # Summarize
    statuses = {}
    total_missing = 0
    total_text_only = 0
    for r in results:
        s = r["status"]
        statuses[s] = statuses.get(s, 0) + 1
        total_missing += r.get("missing", 0)
        total_text_only += r.get("text_only", 0)

    print(f"\n{'='*70}")
    print(f"AUDIT SUMMARY ({len(results)} repos)")
    print(f"{'='*70}")
    for status in ["complete", "minor_gaps", "significant_gaps", "severe_gaps",
                    "text_only_heavy", "ris_fetch_failed", "ris_empty", "no_stf_date", "no_repo"]:
        count = statuses.get(status, 0)
        if count:
            print(f"  {status}: {count}")
    print(f"\nTotal missing sections: {total_missing}")
    print(f"Total text-only sections: {total_text_only}")

    # Save
    output = {
        "metadata": {
            "total_repos_audited": len(results),
            "total_missing_sections": total_missing,
            "total_text_only": total_text_only,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "status_counts": statuses,
        "results": sorted(results, key=lambda r: (0 if r["status"] == "severe_gaps" else 1 if r["status"] == "significant_gaps" else 2, -r.get("missing", 0))),
    }
    with open(AUDIT_OUTPUT, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nAudit saved to {AUDIT_OUTPUT}")


if __name__ == "__main__":
    main()

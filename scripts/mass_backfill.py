#!/usr/bin/env python3
"""Mass paragraph backfill for all repos with gaps.

Uses RIS direct access (confirmed working 2026-05-17) to fetch full
Stammfassung text and fills missing sections into fassung.json.

Strategy:
  1. Reverse gsn_to_abbrev.json → abbrev → GSN
  2. Read paragraph_gaps_audit_full.json → repos needing backfill
  3. For each repo: find StF date from oldest commit → fetch RIS → parse → compare → add missing → commit
  4. Process in parallel batches of N workers

Usage:
  python3 scripts/mass_backfill.py --dry-run          # preview only
  python3 scripts/mass_backfill.py --workers 8        # backfill with 8 parallel workers
  python3 scripts/mass_backfill.py --law ABGB         # single law
  python3 scripts/mass_backfill.py --severity critical # critical only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LAWS_DIR = DATA_DIR / "laws"
GSN_MAP_PATH = DATA_DIR / "gsn_to_abbrev.json"
AUDIT_PATH = DATA_DIR / "paragraph_gaps_audit_full.json"
BACKFILL_LOG = DATA_DIR / "backfill_mass_log.jsonl"
BACKFILL_STATE = DATA_DIR / "backfill_mass_state.json"

RIS_FASSUNG_URL = (
    "https://www.ris.bka.gv.at/GeltendeFassung.wxe"
    "?Abfrage=Bundesnormen&Gesetzesnummer={gsn}&FassungVom={date}"
)
USER_AGENT = "GitForLaw/1.0 (mass-backfill)"
REQUEST_DELAY = 0.35

# Add my-project src for ris_html_parser
MY_PROJECT_SRC = "/mnt/c/Users/notyo/Documents/PhD/my-project/src"
sys.path.insert(0, MY_PROJECT_SRC)


def load_abbrev_to_gsn() -> dict[str, int]:
    """Reverse gsn_to_abbrev.json → {abbrev: gsn}."""
    with open(GSN_MAP_PATH) as f:
        gsn_to_abbrev = json.load(f)
    result: dict[str, int] = {}
    for gsn_str, abbrev in gsn_to_abbrev.items():
        result[abbrev] = int(gsn_str)
    return result


def load_audit_targets(severity_filter: str | None = None) -> list[dict]:
    """Load repos needing backfill from the paragraph gaps audit."""
    with open(AUDIT_PATH) as f:
        audit = json.load(f)
    targets = []
    for repo in audit["repos"]:
        if repo.get("has_gaps") or repo.get("overall_severity") in ("text_only",):
            if severity_filter and repo.get("overall_severity") != severity_filter:
                continue
            targets.append(repo)
    return targets


def find_stf_date(repo_path: Path) -> Optional[str]:
    """Extract Stammfassung date from the oldest commit with a valid date."""
    try:
        output = subprocess.check_output(
            ["git", "-C", str(repo_path), "log", "--format=%H %s", "--reverse"],
            text=True, timeout=10,
        )
        lines = output.strip().split("\n")
        if not lines:
            return None
        # Iterate commits oldest-first until we find one with a valid date
        for line in lines:
            if not line.strip():
                continue
            m = re.search(r"\[(\d{4}-\d{2}-\d{2})\]", line)
            if m:
                return m.group(1)
            m = re.search(r"Fassung\s+vom\s+(\d{4}-\d{2}-\d{2})", line)
            if m:
                return m.group(1)
            m = re.search(r"(\d{4}-\d{2}-\d{2})", line)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def fetch_ris_html(gsn: int, fassung_vom: str) -> Optional[str]:
    """Fetch RIS GeltendeFassung HTML."""
    url = RIS_FASSUNG_URL.format(gsn=gsn, date=fassung_vom)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        if len(html) < 5000:
            return None
        return html
    except Exception as exc:
        return None


def parse_ris_html(html: str) -> dict[str, dict[str, Any]]:
    """Parse RIS HTML into {section_id: section_data}, deduplicated."""
    from git_for_law_austria.ris_html_parser import parse_geltendefassung_html

    raw = parse_geltendefassung_html(html)
    sections: dict[str, dict[str, Any]] = {}
    for s in raw:
        sid = s.get("section_id", "").strip().rstrip(".")
        body = s.get("body", "").strip()
        if not sid or not body:
            continue
        # Normalize "§ 1" → "§_1", "Art. 1" → "Art_1", etc.
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


def _section_sort_key(sid: str):
    """Natural sort: § before Art before Anlage, then by number."""
    s = sid.lower()
    if s.startswith("art"):
        type_order = 1
    elif s.startswith("anlage"):
        type_order = 2
    else:
        type_order = 0
    nums = re.findall(r"\d+", sid)
    num = int(nums[0]) if nums else 0
    sub = int(nums[1]) if len(nums) > 1 else 0
    return (type_order, num, sub, sid)


def _normalize_sid(sid: str) -> str:
    """Aggressively normalize section ID for fuzzy matching."""
    s = sid.strip().lower().replace(" ", "_").replace(".", "")
    s = re.sub(r"_+", "_", s)
    return s


def _extract_numbers(sid: str) -> str:
    """Extract numeric part for fallback matching."""
    nums = re.findall(r"\d+[a-z]?", sid.lower())
    return "_".join(nums) if nums else sid.lower()


def backfill_one(abbrev: str, gsn: int, dry_run: bool = False) -> dict[str, Any]:
    """Backfill a single law's Stammfassung. Returns result dict."""
    result = {
        "abbrev": abbrev, "gsn": gsn, "stf_date": None,
        "ris_sections": 0, "existing": 0, "added": 0,
        "updated": 0, "error": None,
    }

    repo_path = LAWS_DIR / abbrev
    if not repo_path.exists() or not (repo_path / ".git").exists():
        result["error"] = "Repo not found"
        return result

    # Find Stammfassung date
    stf_date = find_stf_date(repo_path)
    if not stf_date:
        result["error"] = "Could not determine StF date"
        return result
    result["stf_date"] = stf_date

    # Load current fassung.json
    fassung_path = repo_path / "fassung.json"
    if not fassung_path.exists():
        result["error"] = "No fassung.json"
        return result
    with open(fassung_path) as f:
        current = json.load(f)
    result["existing"] = len(current)

    # Fetch RIS
    time.sleep(REQUEST_DELAY)
    html = fetch_ris_html(gsn, stf_date)
    if not html:
        # Try without date (current version)
        html = fetch_ris_html(gsn, "")
        if not html:
            result["error"] = "RIS fetch failed"
            return result

    # Parse RIS
    ris_sections = parse_ris_html(html)
    result["ris_sections"] = len(ris_sections)
    if not ris_sections:
        result["error"] = "No sections parsed from RIS"
        return result

    # Build fuzzy lookup for existing sections
    existing_normalized: dict[str, str] = {}
    existing_numbers: dict[str, str] = {}
    for sid in current:
        nsid = _normalize_sid(sid)
        existing_normalized[nsid] = sid
        num = _extract_numbers(sid)
        if num not in existing_numbers:
            existing_numbers[num] = sid

    # Compare and identify missing
    missing: list[tuple[str, dict]] = []
    updated_count = 0

    for ris_sid, ris_sec in ris_sections.items():
        nsid = _normalize_sid(ris_sid)
        num = _extract_numbers(ris_sid)

        matched = None
        if nsid in existing_normalized:
            matched = existing_normalized[nsid]
        elif num in existing_numbers:
            matched = existing_numbers[num]

        if matched:
            ex_sec = current[matched]
            ex_body = ex_sec.get("body", "")
            if len(ex_body) < 50 and len(ris_sec["body"]) > 50:
                if not dry_run:
                    ex_sec["body"] = ris_sec["body"]
                    ex_sec["heading"] = ris_sec.get("heading", ex_sec.get("heading", ""))
                    ex_sec["section_type"] = ris_sec.get("section_type", ex_sec.get("section_type", "Paragraf"))
                updated_count += 1
        else:
            missing.append((ris_sid, ris_sec))

    result["updated"] = updated_count

    # Sort missing
    missing.sort(key=lambda x: _section_sort_key(x[0]))

    # Add missing sections
    for sid, sec in missing:
        if not dry_run:
            current[sid] = sec
        result["added"] += 1

    # Write back
    if not dry_run and (result["added"] > 0 or result["updated"] > 0):
        with open(fassung_path, "w") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)

        # Git commit
        try:
            subprocess.run(
                ["git", "-C", str(repo_path), "add", "fassung.json"],
                check=True, timeout=10,
            )
            msg = (
                f"backfill({abbrev}): +{result['added']} missing, "
                f"~{result['updated']} updated from RIS [{stf_date}]"
            )
            subprocess.run(
                ["git", "-C", str(repo_path), "commit", "-m", msg],
                check=True, timeout=10,
            )
            result["committed"] = True
        except subprocess.CalledProcessError:
            result["error"] = "Git commit failed"

    return result


def worker(args: tuple) -> dict[str, Any]:
    """Pool worker. Accepts (abbrev, gsn, dry_run)."""
    abbrev, gsn, dry_run = args
    try:
        return backfill_one(abbrev, gsn, dry_run)
    except Exception as exc:
        return {"abbrev": abbrev, "gsn": gsn, "error": str(exc), "traceback": traceback.format_exc()}


def load_state() -> set[str]:
    """Load set of already-processed abbrevs."""
    if BACKFILL_STATE.exists():
        with open(BACKFILL_STATE) as f:
            return set(json.load(f))
    return set()


def save_state(done: set[str]) -> None:
    with open(BACKFILL_STATE, "w") as f:
        json.dump(sorted(done), f)


def log_result(r: dict) -> None:
    with open(BACKFILL_LOG, "a") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Mass paragraph backfill from RIS")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--law", type=str, help="Single law abbreviation")
    parser.add_argument("--severity", type=str, choices=["critical", "high", "medium", "low", "text_only"],
                        help="Only process repos with this severity")
    parser.add_argument("--limit", type=int, default=0, help="Max repos to process (0 = all)")
    parser.add_argument("--resume", action="store_true", help="Skip already-processed repos")
    parser.add_argument("--targets-file", type=str, help="JSON file with list of abbrevs to backfill")
    parser.add_argument("--all", action="store_true", help="Process all repos (ignore audit)")
    args = parser.parse_args()

    # Load mappings
    abbrev_to_gsn = load_abbrev_to_gsn()
    print(f"Loaded {len(abbrev_to_gsn)} abbrev→GSN mappings")

    # Determine targets
    if args.law:
        gsn = abbrev_to_gsn.get(args.law)
        if not gsn:
            print(f"ERROR: No GSN found for '{args.law}'")
            sys.exit(1)
        targets = [{"repo": args.law}]
    elif args.targets_file:
        with open(args.targets_file) as f:
            abbrevs = json.load(f)
        targets = [{"repo": a, "overall_severity": "targeted"} for a in abbrevs]
        print(f"Loaded {len(targets)} repos from targets file")
    elif getattr(args, 'all', False):
        targets = [{"repo": d.name, "overall_severity": "all"}
                   for d in sorted(LAWS_DIR.iterdir())
                   if d.is_dir() and (d / ".git").exists()]
        print(f"Loaded all {len(targets)} repos")
    else:
        targets = load_audit_targets(args.severity)
        print(f"Loaded {len(targets)} repos needing backfill" + (f" (severity={args.severity})" if args.severity else ""))

    # Sort by severity: critical first, then high, etc.
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "text_only": 4, "targeted": 0, "all": 0, "none": 5}
    targets.sort(key=lambda t: severity_order.get(t.get("overall_severity", "none"), 5))

    # Apply limit
    if args.limit > 0:
        targets = targets[:args.limit]

    # Resume
    done = load_state() if args.resume else set()

    # Build work items
    work_items = []
    skipped_no_gsn = 0
    skipped_done = 0
    for t in targets:
        abbrev = t["repo"]
        if args.resume and abbrev in done:
            skipped_done += 1
            continue
        gsn = abbrev_to_gsn.get(abbrev)
        if not gsn:
            skipped_no_gsn += 1
            continue
        work_items.append((abbrev, gsn, args.dry_run))

    print(f"Work items: {len(work_items)} (skipped: {skipped_done} done, {skipped_no_gsn} no-GSN)")
    if not work_items:
        print("Nothing to do.")
        return

    total = len(work_items)
    total_added = 0
    total_updated = 0
    errors = 0
    processed = 0

    if args.dry_run:
        print("\n--- DRY RUN ---")

    with Pool(processes=args.workers) as pool:
        for r in pool.imap_unordered(worker, work_items):
            processed += 1
            done.add(r["abbrev"])

            if r.get("error"):
                errors += 1
                err_msg = r["error"]
                if "traceback" in r and r["traceback"]:
                    err_short = r["traceback"].split("\n")[-2] if "\n" in r["traceback"] else r["traceback"]
                else:
                    err_short = err_msg
                print(f"[{processed}/{total}] {r['abbrev']} ERROR: {err_short}")
            else:
                added = r.get("added", 0)
                updated = r.get("updated", 0)
                total_added += added
                total_updated += updated
                marker = ""
                if added > 0:
                    marker = f" +{added}"
                if updated > 0:
                    marker += f" ~{updated}"
                print(f"[{processed}/{total}] {r['abbrev']} (GSN {r['gsn']}, StF {r.get('stf_date', '?')}) RIS:{r.get('ris_sections',0)} repo:{r.get('existing',0)}{marker}")

            log_result(r)

            # Save state periodically
            if processed % 50 == 0:
                save_state(done)

    save_state(done)

    print(f"\n{'='*60}")
    print(f"BACKFILL COMPLETE")
    print(f"  Processed: {processed}")
    print(f"  Sections added: {total_added}")
    print(f"  Sections updated (text-only → full): {total_updated}")
    print(f"  Errors: {errors}")
    print(f"  Log: {BACKFILL_LOG}")


if __name__ == "__main__":
    main()

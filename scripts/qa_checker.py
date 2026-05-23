#!/usr/bin/env python3
"""Comprehensive QA checker for all law repos.

Checks every law via BFF API for:
  A) Web-GUI correctness (section IDs, bodies, sorting, contiguity)
  B) CLI diff tool correctness
  C) Miscellaneous issues (malformed IDs, empty data, regressions)

Output: data/qa_report.json with categorized issues.
"""

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAWS_DIR = PROJECT_ROOT / "data" / "laws"
REPORT_PATH = PROJECT_ROOT / "data" / "qa_report.json"
BFF_BASE = "http://127.0.0.1:8081"


def bff_get(path: str, timeout: int = 30):
    import urllib.parse
    # URL-encode the path to handle spaces, unicode, special chars
    encoded_path = urllib.parse.quote(path, safe="/?=&")
    try:
        with urllib.request.urlopen(f"{BFF_BASE}{encoded_path}", timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)[:120]}


def check_law(abbrev: str) -> dict:
    """Run all checks on one law. Returns dict of issues found."""
    issues = []
    info = bff_get(f"/api/laws/{abbrev}")
    if "error" in info:
        return {"abbrev": abbrev, "issues": [{"severity": "CRITICAL", "check": "api_error", "detail": info["error"]}]}

    sections_total = info.get("sections", 0)
    versions_count = info.get("versions_count", 0)
    versions_list = info.get("versions_list", [])

    # ---- A) Stammfassung checks ----
    stf_date = versions_list[-1]["fassung_vom"] if versions_list else None
    if stf_date:
        stf_sections = bff_get(f"/api/laws/{abbrev}/sections?date={stf_date}")
        if isinstance(stf_sections, list):
            issues.extend(_check_sections(abbrev, stf_sections, "StF"))

    # Also check latest version
    if versions_list and versions_list[0]["fassung_vom"] != stf_date:
        latest_sections = bff_get(f"/api/laws/{abbrev}/sections?date={versions_list[0]['fassung_vom']}")
        if isinstance(latest_sections, list):
            issues.extend(_check_sections(abbrev, latest_sections, "latest"))

    # ---- B) Diff checks ----
    if versions_count >= 2 and stf_date and versions_list:
        latest_date = versions_list[0]["fassung_vom"]
        if latest_date != stf_date:
            diff = bff_get(f"/api/laws/{abbrev}/diff?from={stf_date}&to={latest_date}")
            if isinstance(diff, dict) and "changed_sections" in diff:
                changed = diff["changed_sections"]
                unchanged = diff["unchanged_sections"]
                if not changed and not unchanged:
                    issues.append({"severity": "WARNING", "check": "empty_diff", "detail": "Diff has no changed or unchanged sections"})
                for cs in changed[:3]:  # spot-check first 3
                    if not cs.get("new_body") and not cs.get("old_body"):
                        issues.append({"severity": "WARNING", "check": "diff_empty_body", "detail": f"Section {cs['section_id']} has empty old and new body"})
                    if cs["section_id"].startswith("Section-"):
                        issues.append({"severity": "WARNING", "check": "diff_section_n", "detail": f"Diff contains Section-N: {cs['section_id']}"})

    # ---- C) Misc checks ----
    if sections_total < 5 and versions_count > 0:
        issues.append({"severity": "CRITICAL", "check": "too_few_sections", "detail": f"Only {sections_total} sections with {versions_count} versions"})

    repo_path = LAWS_DIR / abbrev
    if repo_path.exists():
        issues.extend(_check_git_health(abbrev, repo_path))

    return {"abbrev": abbrev, "sections": sections_total, "versions": versions_count, "issues": issues}


def _check_sections(abbrev: str, sections: list, label: str) -> list:
    """Check a section list for formatting, sorting, contiguity issues."""
    issues = []
    if not sections:
        return [{"severity": "CRITICAL", "check": f"{label}_empty", "detail": "No sections returned"}]

    section_ids = [s["section_id"] for s in sections]
    bodies = [s.get("body", "") for s in sections]

    # Count Section-N and Text IDs
    sn_count = sum(1 for sid in section_ids if sid.startswith("Section-"))
    text_count = sum(1 for sid in section_ids if sid == "Text")
    total = len(section_ids)

    if sn_count > 0:
        sn_pct = sn_count / total
        severity = "CRITICAL" if sn_pct > 0.1 else "WARNING" if sn_pct > 0 else "INFO"
        if sn_pct > 0:
            issues.append({"severity": severity, "check": f"{label}_section_n",
                          "detail": f"{sn_count}/{total} Section-N IDs ({sn_pct:.0%})"})

    if text_count > 0:
        issues.append({"severity": "WARNING", "check": f"{label}_text_ids",
                      "detail": f"{text_count} 'Text' section IDs"})

    # Check for empty/short bodies
    short_bodies = []
    for s in sections:
        body = s.get("body", "").strip()
        if not body:
            short_bodies.append(f"{s['section_id']}: empty")
        elif len(body) < 20:
            short_bodies.append(f"{s['section_id']}: {len(body)} chars")
    if short_bodies:
        issues.append({"severity": "WARNING", "check": f"{label}_short_bodies",
                      "detail": f"{len(short_bodies)} short/empty: {short_bodies[:5]}"})

    # Check sort order (natural sort: §_2 before §_10)
    def sort_key(sid):
        parts = sid.split("_", 1)
        if len(parts) == 2:
            try:
                return (0, int(parts[1]), parts[0])
            except ValueError:
                return (1, 0, sid)
        return (2, 0, sid)

    sorted_ids = sorted(section_ids, key=sort_key)
    if section_ids != sorted_ids:
        out_of_order = []
        for i, (actual, expected) in enumerate(zip(section_ids, sorted_ids)):
            if actual != expected:
                out_of_order.append(f"pos {i}: {actual} should be {expected}")
        if out_of_order:
            issues.append({"severity": "WARNING", "check": f"{label}_sort_order",
                          "detail": f"{len(out_of_order)} out of order: {out_of_order[:3]}"})

    # Check for malformed IDs
    malformed = []
    for sid in section_ids:
        if sid.startswith("Section-") or sid == "Text":
            continue
        # Check for trailing punctuation, mid-ID underscores from old parser
        if re.search(r'[,;]$', sid):
            malformed.append(f"{sid}: trailing punctuation")
        elif re.search(r'[._]Paragraph', sid):
            malformed.append(f"{sid}: contains Paragraph text")
        elif re.search(r'\.\s', sid):
            malformed.append(f"{sid}: contains spaces/dots")
    if malformed:
        issues.append({"severity": "INFO", "check": f"{label}_malformed_ids",
                      "detail": f"{len(malformed)} malformed: {malformed[:5]}"})

    # Check paragraph contiguity for § sections
    para_nums = []
    for sid in section_ids:
        m = re.match(r"^§_(\d+[a-z]?)$", sid)
        if m:
            try:
                num = int(re.sub(r'[a-z]$', '', m.group(1)))
                para_nums.append(num)
            except ValueError:
                pass

    if para_nums and len(para_nums) >= 5:
        para_nums.sort()
        gaps = []
        for i in range(len(para_nums) - 1):
            gap = para_nums[i+1] - para_nums[i]
            if gap > 5:  # More than 5 missing paragraphs = suspicious
                gaps.append(f"§ {para_nums[i]} → § {para_nums[i+1]} (gap of {gap})")
        if gaps:
            issues.append({"severity": "INFO", "check": f"{label}_gaps",
                          "detail": f"{len(gaps)} large gaps: {gaps[:5]}"})

    # Check for duplicate IDs
    seen = {}
    for sid in section_ids:
        seen[sid] = seen.get(sid, 0) + 1
    dups = [sid for sid, count in seen.items() if count > 1]
    if dups:
        issues.append({"severity": "CRITICAL", "check": f"{label}_duplicates",
                      "detail": f"Duplicate IDs: {dups[:5]}"})

    return issues


def _check_git_health(abbrev: str, repo_path: Path) -> list:
    """Check git repo health."""
    issues = []
    try:
        commits = subprocess.check_output(
            ["git", "-C", str(repo_path), "log", "--format=%H|%s"],
            text=True, timeout=10,
        ).strip().split("\n")

        # Check for missing fassung_vom dates
        bad_msgs = 0
        for line in commits:
            if "|" not in line:
                continue
            _, msg = line.split("|", 1)
            if not re.search(r"\[\d{4}-\d{2}-\d{2}\]", msg):
                bad_msgs += 1

        if len(commits) > 1 and bad_msgs > len(commits) * 0.1:
            issues.append({"severity": "WARNING", "check": "commit_messages",
                          "detail": f"{bad_msgs}/{len(commits)} commits missing fassung_vom date"})

        # Check HEAD fassung.json exists and is valid
        try:
            head_data = subprocess.check_output(
                ["git", "-C", str(repo_path), "show", "HEAD:fassung.json"],
                text=True, timeout=10,
            )
            head = json.loads(head_data)
            if not head:
                issues.append({"severity": "CRITICAL", "check": "empty_fassung", "detail": "HEAD fassung.json is empty"})
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            issues.append({"severity": "CRITICAL", "check": "missing_fassung", "detail": "Cannot read HEAD fassung.json"})

    except subprocess.CalledProcessError as e:
        issues.append({"severity": "CRITICAL", "check": "git_error", "detail": str(e)[:100]})

    return issues


def main():
    print("Loading law list...")
    laws = bff_get("/api/laws", timeout=60)
    if isinstance(laws, dict) and "error" in laws:
        print(f"ERROR: BFF not reachable: {laws['error']}")
        sys.exit(1)

    print(f"Checking {len(laws)} laws...")

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_laws": len(laws),
        "laws": [],
        "summary": {"CRITICAL": 0, "WARNING": 0, "INFO": 0, "OK": 0},
    }

    critical_laws = []
    warning_laws = []
    info_laws = []

    for i, law in enumerate(laws):
        abbrev = law["abbrev"]
        if (i + 1) % 100 == 0:
            print(f"  [{i+1}/{len(laws)}] {abbrev}...")

        result = check_law(abbrev)
        report["laws"].append(result)

        max_severity = "OK"
        for issue in result["issues"]:
            sev = issue["severity"]
            if sev == "CRITICAL" and max_severity != "CRITICAL":
                max_severity = "CRITICAL"
            elif sev == "WARNING" and max_severity not in ("CRITICAL",):
                max_severity = "WARNING"
            elif sev == "INFO" and max_severity not in ("CRITICAL", "WARNING"):
                max_severity = "INFO"

        if max_severity == "CRITICAL":
            critical_laws.append(result)
        elif max_severity == "WARNING":
            warning_laws.append(result)
        elif max_severity == "INFO":
            info_laws.append(result)

        report["summary"][max_severity] += 1

    # Write report
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"QA Report: {REPORT_PATH}")
    print(f"Total laws: {len(laws)}")
    print(f"  CRITICAL: {len(critical_laws)}")
    print(f"  WARNING:  {len(warning_laws)}")
    print(f"  INFO:     {len(info_laws)}")
    print(f"  OK:       {report['summary']['OK']}")
    print(f"{'='*60}")

    # Print critical issues
    if critical_laws:
        print(f"\nCRITICAL ISSUES:")
        for r in critical_laws:
            for issue in r["issues"]:
                if issue["severity"] == "CRITICAL":
                    print(f"  {r['abbrev']}: [{issue['check']}] {issue['detail']}")

    # Summary of warning categories
    warn_categories = defaultdict(int)
    for r in warning_laws:
        for issue in r["issues"]:
            if issue["severity"] == "WARNING":
                warn_categories[issue["check"]] += 1

    if warn_categories:
        print(f"\nWARNING categories:")
        for cat, count in sorted(warn_categories.items(), key=lambda x: -x[1]):
            print(f"  {cat}: {count}")

    return report


if __name__ == "__main__":
    main()

"""Generate data/laws_index.json to avoid scanning 1350+ git repos on first request."""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAWS_DIR = ROOT / "data" / "laws"


def load_names():
    gsn_to_abbrev_path = ROOT / "data" / "gsn_to_abbrev.json"
    catalog_path = ROOT / "data" / "law_catalog_merged.json"
    synthetic_path = ROOT / "data" / "synthetic_abbrevs.json"
    with open(gsn_to_abbrev_path) as f:
        gsn_to_abbrev = json.load(f)
    with open(catalog_path) as f:
        catalog = json.load(f)
    abbrev_to_gsn = {}
    for gsn, abbrev in gsn_to_abbrev.items():
        if abbrev not in abbrev_to_gsn:
            abbrev_to_gsn[abbrev] = gsn
    name_cache = {}
    for abbrev, gsn in abbrev_to_gsn.items():
        entry = catalog.get(gsn, {})
        if name := entry.get("name", ""):
            name_cache[abbrev] = name
    with open(synthetic_path) as f:
        synthetic = json.load(f)
    return name_cache, synthetic


def git_log(repo):
    try:
        output = subprocess.check_output(
            ["git", "-C", str(repo), "log", "--format=%H %s"],
            stderr=subprocess.DEVNULL, text=True, timeout=10,
        )
    except subprocess.CalledProcessError:
        return []
    commits = []
    for line in output.strip().split("\n"):
        if line:
            parts = line.split(" ", 1)
            commits.append((parts[0], parts[1] if len(parts) > 1 else ""))
    return commits


def git_count_sections(repo, commit_hash):
    try:
        output = subprocess.check_output(
            ["git", "-C", str(repo), "show", f"{commit_hash}:fassung.json"],
            stderr=subprocess.DEVNULL, text=True, timeout=10,
        )
        return len(json.loads(output))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return 0


def main():
    name_cache, synthetic = load_names()
    laws = []
    total = 0
    for d in sorted(LAWS_DIR.iterdir()):
        if not d.is_dir() or not (d / ".git").exists():
            continue
        total += 1
        abbrev = d.name
        commits = git_log(d)
        newest = None
        if commits:
            dates = []
            for _, msg in commits:
                m = re.match(r"^(?:\S+\s+)?(\S+)\s+\[([^\]]+)\]:\s*(.*)", msg)
                if m:
                    dates.append(m.group(2))
            if dates:
                newest = max(dates)
        sections = git_count_sections(d, commits[0][0]) if commits else 0
        laws.append({
            "abbrev": abbrev,
            "name": name_cache.get(abbrev, ""),
            "versions": len(set(dates)) if commits else 0,
            "sections": sections,
            "newest_fassung_vom": newest,
            "synthetic": abbrev in synthetic,
        })

    out_path = ROOT / "data" / "laws_index.json"
    with open(out_path, "w") as f:
        json.dump(laws, f, ensure_ascii=False)
    print(f"Wrote {total} laws to {out_path}")


if __name__ == "__main__":
    main()

"""Clean rebuild: delete old law repos and rebuild from scratch.

Usage: python3 scripts/clean_rebuild.py [--workers N] [--dry-run]
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None

BASE = Path(__file__).resolve().parent.parent
LAWS_DIR = BASE / "data" / "laws"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/rebuild_clean.json")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", type=str, default="", help="Comma-separated abbrevs")
    parser.add_argument("--skip-delete", action="store_true", help="Skip deletion (rerun after failure)")
    args = parser.parse_args()

    with open(BASE / "data" / "gsn_to_abbrev.json") as f:
        gsn_to_abbrev = json.load(f)

    if args.only:
        abbrev_to_gsn = {v: k for k, v in gsn_to_abbrev.items()}
        laws = [{"gsn": abbrev_to_gsn[a.strip()], "name": a.strip()} for a in args.only.split(",")]
    else:
        with open(BASE / args.input) as f:
            data = json.load(f)
        laws = data["laws"]

    print(f"Clean rebuild: {len(laws)} laws")

    if not args.skip_delete:
        deleted = 0
        for law in laws:
            abbrev = law["name"]
            law_dir = LAWS_DIR / abbrev
            if law_dir.exists():
                if args.dry_run:
                    print(f"  [dry-run] would delete {law_dir}")
                else:
                    shutil.rmtree(law_dir)
                deleted += 1

        print(f"Deleted {deleted} existing law directories")
        if args.dry_run:
            print("Dry run — not rebuilding")
            return

    print(f"Starting pipeline with {args.workers} workers...")
    gsns = ",".join(law["gsn"] for law in laws)

    cmd = [
        sys.executable, str(BASE / "scripts" / "batch_pipeline.py"),
        "--workers", str(args.workers),
        "--only", gsns,
    ]
    start = time.time()
    result = subprocess.run(cmd, cwd=str(BASE))
    elapsed = time.time() - start

    print(f"\nRebuild completed in {elapsed/60:.1f} minutes (exit code {result.returncode})")


if __name__ == "__main__":
    main()

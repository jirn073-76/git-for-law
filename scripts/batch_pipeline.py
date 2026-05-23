"""Batch pipeline runner — process multiple laws in parallel.

Usage: python3 scripts/batch_pipeline.py [--workers N] [--max-versions V]
"""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Force unbuffered output for progress visibility when piped
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None


def run_pipeline_for_gsn(gsn, max_versions=None):
    """Run the pipeline for a single GSN. Returns (gsn, success, result_dict)."""
    start = time.time()
    try:
        from git_for_law_austria.pipeline import Pipeline

        p = Pipeline()
        result = p.run(gsn=gsn, max_versions=max_versions)
        elapsed = time.time() - start
        return (
            gsn,
            True,
            {
                "abbrev": result.law_abbrev,
                "versions_processed": result.versions_processed,
                "versions_committed": result.versions_committed,
                "sections_parsed": result.sections_parsed,
                "errors": result.errors,
                "elapsed_s": round(elapsed, 1),
            },
        )
    except Exception as e:
        elapsed = time.time() - start
        return (gsn, False, {"error": str(e), "elapsed_s": round(elapsed, 1)})


def main():
    parser = argparse.ArgumentParser(description="Batch pipeline runner")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers")
    parser.add_argument("--max-versions", type=int, default=None, help="Max versions per law")
    parser.add_argument("--input", type=str, default="data/final_gsn_list.json", help="Final GSN list input")
    parser.add_argument("--limit", type=int, default=0, help="Limit to first N laws (0 = all)")
    parser.add_argument("--only", type=str, default="", help="Comma-separated GSNs to process")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent.parent

    if args.only:
        gsns = [g.strip() for g in args.only.split(",") if g.strip()]
    else:
        with open(base / args.input) as f:
            data = json.load(f)
        entries = list(data.get("laws", data.get("matched", {})))
        if not entries and isinstance(data, dict):
            # Maybe it's a flat dict keyed by GSN
            entries = list(data.values())
        if isinstance(entries, dict):
            entries = list(entries.values())
        if args.limit and args.limit > 0:
            entries = entries[: args.limit]
        gsns = [e["gsn"] for e in entries]

    print(f"Running pipeline for {len(gsns)} laws with {args.workers} workers")
    print(f"Max versions per law: {args.max_versions or 'unlimited'}")

    results = {}
    completed = 0
    failed = 0
    checkpoint_path = base / "data" / "batch_results_checkpoint.json"

    def save_checkpoint():
        with open(checkpoint_path, "w") as f:
            json.dump(
                {
                    "total": len(gsns),
                    "completed": completed,
                    "failed": failed,
                    "results": results,
                    "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

    if args.workers == 1:
        for gsn in gsns:
            gsn, ok, info = run_pipeline_for_gsn(gsn, args.max_versions)
            results[gsn] = info
            if ok:
                completed += 1
                print(f"  OK  {gsn} ({info.get('abbrev','?')}): "
                      f"{info['versions_committed']} versions in {info['elapsed_s']}s", flush=True)
            else:
                failed += 1
                print(f"  FAIL {gsn}: {info.get('error','?')}", flush=True)
            if (completed + failed) % 10 == 0:
                save_checkpoint()
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(run_pipeline_for_gsn, gsn, args.max_versions): gsn
                for gsn in gsns
            }
            for future in as_completed(futures):
                gsn, ok, info = future.result()
                results[gsn] = info
                if ok:
                    completed += 1
                    print(
                        f"  [{completed+failed}/{len(gsns)}] OK  {gsn} "
                        f"({info.get('abbrev','?')}): "
                        f"{info['versions_committed']} versions in {info['elapsed_s']}s",
                        flush=True,
                    )
                else:
                    failed += 1
                    print(
                        f"  [{completed+failed}/{len(gsns)}] FAIL {gsn}: "
                        f"{info.get('error','?')}",
                        flush=True,
                    )
                if (completed + failed) % 10 == 0:
                    save_checkpoint()

    # Save results
    out = base / "data" / "batch_results.json"
    with open(out, "w") as f:
        json.dump(
            {
                "total": len(gsns),
                "completed": completed,
                "failed": failed,
                "results": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\nDone: {completed} completed, {failed} failed")
    print(f"Results saved to {out}")


if __name__ == "__main__":
    main()

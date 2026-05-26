# Git for Law — Austria

Austrian federal law tracked in git. Every Fassung (version) of a Bundesgesetz is a commit. The pipeline fetches structured NOR XML from the RIS, parses it into versioned sections with body blocks, and commits the result — so you get meaningful diffs between any two points in time.

It's a personal experiment at the intersection of law and version control. Built by [Dionis Ramadani](mailto:d.ramadani@ieee.org).

## What this actually does

The Austrian government publishes federal law through the RIS (Rechtsinformationssystem des Bundes). Each law exists in multiple Fassungen — versions that were in force at different dates. RIS lets you view a single Fassung as HTML. It doesn't give you diffs.

This pipeline:

1. Pulls metadata from the OGD API v2.6 (`data.bka.gv.at`)
2. Fetches structured NOR XML for each Fassung (RIS Bundesnormen)
3. Parses the XML into structured JSON — sections, headings, paragraphs, lists
4. Commits each Fassung to a git repository under `data/laws/<Abbreviation>/`

The NOR XML pipeline produces clean section IDs, proper heading/body separation, and structured `body_blocks` that preserve list formatting.

The result: `git log` shows the amendment history. `git diff 2017-01-01..2018-01-01` shows what changed between two dates. Each commit message records the Fassung date and the amendment reference (Bundesgesetzblatt number).

## Data

The data is Austrian federal law, fetched from the official OGD endpoint and licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.de) by the Federal Chancellory. The dataset in this repo is frozen as of May 2026.

No warranty. No legal claims can be derived from this data.

## Quick start

```bash
pip install -e .
```

### Run the pipeline for a single law

```python
from git_for_law_austria.nor_xml import NORCache, build_fassung
import requests

session = requests.Session()
session.headers.update({"User-Agent": "git-for-law/0.1"})
cache = NORCache("data/nor_cache")
fassung = build_fassung("10001622", "2026-01-01", cache, session)  # ABGB
print(f"Built {len(fassung)} sections")
```

### Batch process all laws

```bash
python scripts/nor_batch_pipeline.py --workers 7
```

The NOR pipeline is resumable — it checkpoints progress to `data/nor_checkpoint.json`.
Re-run the same command to pick up where it left off.

```bash
python scripts/nor_batch_pipeline.py --only ABGB     # single law
python scripts/nor_batch_pipeline.py --reset          # clear checkpoint, start fresh
python scripts/nor_batch_pipeline.py --workers 5      # custom worker count
```

### Rebuild a specific law

```bash
python scripts/clean_rebuild.py --only ABGB --skip-delete
```

### Diff two versions from the command line

```bash
python -m git_for_law_austria.diff ABGB 2017-01-01 2018-01-01
```

### Start the web UI

```bash
pip install fastapi uvicorn
python bff/server.py
# → http://localhost:8081
```

## Repository structure

```
src/git_for_law_austria/   # Python package
  nor_xml.py               #   NOR XML fetcher and parser
  diff.py                  #   CLI diff viewer
scripts/                   # Operational scripts
  nor_batch_pipeline.py    #   Main entry: NOR XML batch processing (resumable)
  build_index.py           #   Build the master law index
  scan_all_gsns.py         #   Scan all GSNs from the catalog
  match_law_index.py       #   Match scanned GSNs against the index
  disambiguate_and_finalize.py  #   Finalize the GSN list
  verify_all_laws.py       #   Verify all processed laws
  qa_checker.py            #   Quality checks
bff/server.py              # FastAPI backend (serves the frontend + REST API)
frontend/                  # Static web UI
tests/                     # pytest suite
  test_diff.py             #   CLI diff viewer tests
  test_data_quality.py     #   Data quality checks on law repos
data/                      # Config files and input data
  gsn_to_abbrev.json       #   Maps GSN numbers to abbreviations
  final_gsn_list.json      #   List of laws to process
  law_catalog_merged.json  #   Full law name catalog
  synthetic_abbrevs.json   #   Synthetic abbreviations for uncatalogued laws
  laws_index.json          #   Pre-built law index (used by the BFF)
```

## How the NOR XML parser works

The RIS publishes each legal provision as a NOR (Norm) XML document. The parser (`nor_xml.py`) handles:

- **Sections (§)**: Standard paragraphs — `§ 1.` through `§ N.`
- **Articles (Art.)**: Compound structures — `Art. I § 1.`, standalone `Art. X.`
- **Anlagen**: Annexes and appendices
- **Lists**: Ordered lists (`ziffernliste`), lettered lists (`literaliste`), Roman numeral lists (`aufzaehlung`), dash lists (`strichliste`)
- **Nested structures**: Lists within lists, `schlussteil` closing elements

Section IDs are derived from APA metadata, `gldsym` text, or heading/body patterns and normalized to: `§_1`, `Art_I_§_2`, `Anlage_3`.

Each section stores `body_blocks` — a structured representation preserving list nesting — alongside the plain-text `body`.

## Dependencies

- Python >= 3.10
- `requests` — HTTP client for OGD API and RIS
- `gitpython` — git repository management
- Optional: `fastapi`, `uvicorn` — for the web UI

## License

This project is licensed under the GNU Affero General Public License v3.0 — see [LICENSE](LICENSE).

The legal data served by this software is from the Austrian RIS/OGD and is separately licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.de) by the Federal Chancellory (Bundeskanzleramt).

Datenquelle: Bundeskanzleramt — RIS/OGD · CC BY 4.0

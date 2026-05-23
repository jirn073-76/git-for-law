# Git for Law — Austria

Austrian federal law tracked in git. Every Fassung (version) of a Bundesgesetz is a commit. The pipeline fetches RIS HTML, parses it into structured sections, and commits the result — so you get meaningful diffs between any two points in time.

It's a personal experiment at the intersection of law and version control. Built by [Dionis Ramadani](mailto:d.ramadani@ieee.org).

## What this actually does

The Austrian government publishes federal law through the RIS (Rechtsinformationssystem des Bundes). Each law exists in multiple Fassungen — versions that were in force at different dates. RIS lets you view a single Fassung as HTML. It doesn't give you diffs.

This pipeline:

1. Pulls metadata from the OGD API v2.6 (`data.bka.gv.at`)
2. Fetches the full HTML for each Fassung (RIS GeltendeFassung)
3. Parses the HTML into structured JSON — sections, headings, paragraphs
4. Commits each Fassung to a git repository under `data/laws/<Abbreviation>/`

The result: `git log` shows the amendment history. `git diff 2017-01-01..2018-01-01` shows what changed between two dates. Each commit message records the Fassung date and the amendment reference (Bundesgesetzblatt number).

## Data

The data is Austrian federal law, fetched from the official OGD endpoint and licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.de) by the Bundesministerium fur Finanzen. The dataset in this repo is frozen as of May 2026.

No warranty. No legal claims can be derived from this data.

## Quick start

```bash
pip install -e .
```

### Run the pipeline for a single law

```python
from git_for_law_austria.pipeline import Pipeline

p = Pipeline()
result = p.run(gsn="10001622")  # ABGB
print(f"Processed {result.versions} versions, {result.sections} sections")
```

### Batch process all laws

```bash
python scripts/batch_pipeline.py --input data/final_gsn_list.json --workers 4
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
  fetcher.py               #   OGD API v2.6 metadata client
  ogd_content_fetcher.py   #   NOR content fetcher
  wayback_fetcher.py       #   Wayback Machine fallback fetcher
  ris_parser.py            #   RIS HTML → structured sections parser
  pipeline.py              #   Full pipeline orchestrator
  harness.py               #   Quality scoring (content, diffs, coverage)
  diff.py                  #   CLI diff viewer
scripts/                   # Operational scripts
  batch_pipeline.py        #   Main entry: process all laws in parallel
  clean_rebuild.py         #   Selective rebuild of broken repos
  build_index.py           #   Build the master law index
  scan_all_gsns.py         #   Scan all GSNs from the catalog
  match_law_index.py       #   Match scanned GSNs against the index
  disambiguate_and_finalize.py  #   Finalize the GSN list
  verify_all_laws.py       #   Verify all processed laws
  qa_checker.py            #   Quality checks
  comprehensive_audit.py   #   Full RIS-backed audit
  mass_backfill.py         #   Mass paragraph backfill
bff/server.py              # FastAPI backend (serves the frontend + REST API)
frontend/                  # Static web UI
tests/                     # pytest suite (2818 tests)
data/                      # Config files and input data
  gsn_to_abbrev.json       #   Maps GSN numbers to abbreviations
  final_gsn_list.json      #   List of laws to process
  law_catalog_merged.json  #   Full law name catalog
  synthetic_abbrevs.json   #   Synthetic abbreviations for uncatalogued laws
  laws_index.json          #   Pre-built law index (used by the BFF)
```

## How the parser works

RIS GeltendeFassung HTML has a consistent structure but isn't machine-readable in any useful way. The parser (`ris_parser.py`) handles several cases:

- **Standard paragraphs**: `§ 1. (1) Text...` — sections with numbered Absatze
- **Articles**: `Artikel I § 1. (1) Text...` — compound article + paragraph structures
- **Standalone articles**: `Artikel X.` with body text but no subsections (common in Schluss- und Ubergangsbestimmungen)
- **Anlagen**: Annexes and appendices
- **Old RIS format**: Pre-2015 RIS used a different markup with `Paragraph` labels

Section IDs are normalized to a consistent scheme: `§_1`, `Art_I_§_2`, `Anlage_3`.

## Dependencies

- Python >= 3.10
- `requests` — HTTP client for OGD API and RIS
- `gitpython` — git repository management
- Optional: `fastapi`, `uvicorn` — for the web UI

## License

This project is licensed under the GNU Affero General Public License v3.0 — see [LICENSE](LICENSE).

The legal data served by this software is from the Austrian RIS/OGD and is separately licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.de) by the Bundesministerium fur Finanzen.

Datenquelle: Bundesministerium fur Finanzen — RIS/OGD · CC BY 4.0

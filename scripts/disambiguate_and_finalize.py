"""Disambiguate ambiguous matches and produce final GSN list for batch processing."""

import json
import re
from pathlib import Path


def extract_year(name):
    """Extract a 4-digit year from a law name, preferring years after 1900."""
    years = re.findall(r'\b((?:19|20)\d{2})\b', name)
    if years:
        return max(int(y) for y in years)
    return None


def disambiguate(ambiguous):
    """Resolve ambiguous matches with a heuristic strategy.

    Matches are dicts: {gsn, catalog_name, catalog_abbrev, catalog_typ, score}
    """
    resolved = {}
    still_ambiguous = {}

    for key, matches in ambiguous.items():
        j_name = key.split("|")[0]
        j_abbrev = key.split("|")[1]
        j_year = extract_year(j_name) or extract_year(j_abbrev)

        # Rule 1: exact abbreviation match
        exact_abbrev = [
            m for m in matches
            if m.get('catalog_abbrev', '').strip().upper() == j_abbrev.strip().upper()
        ]
        if len(exact_abbrev) == 1:
            resolved[key] = exact_abbrev[0]
            continue

        # Rule 2: matching year
        if j_year:
            year_matches = []
            for m in matches:
                c_name = m.get('catalog_name', '')
                c_year = extract_year(c_name)
                if c_year and c_year == j_year:
                    year_matches.append(m)
            if len(year_matches) == 1:
                resolved[key] = year_matches[0]
                continue
            elif len(year_matches) > 1:
                year_matches.sort(key=lambda x: x['gsn'], reverse=True)
                resolved[key] = year_matches[0]
                continue

        # Rule 3: highest GSN (most recent = highest number)
        matches.sort(key=lambda x: x['gsn'], reverse=True)
        resolved[key] = matches[0]

    return resolved, still_ambiguous


def main():
    base = Path(__file__).resolve().parent.parent

    with open(base / 'data' / 'law_index_matched.json') as f:
        data = json.load(f)

    print(f"Input: {len(data['matched'])} matched, "
          f"{len(data['ambiguous'])} ambiguous, "
          f"{len(data['unmatched'])} unmatched")

    resolved, still_amb = disambiguate(data['ambiguous'])

    # Merge resolved into matched (both are dicts from JSON)
    all_matched = dict(data['matched'])
    for key, match_dict in resolved.items():
        all_matched[key] = match_dict

    print(f"After disambiguation: {len(all_matched)} matched, "
          f"{len(still_amb)} still ambiguous")

    # Build final GSN list
    final = {}
    for key, match_dict in all_matched.items():
        gsn = match_dict['gsn']
        if gsn in final:
            if match_dict.get('score', 0) > final[gsn].get('score', 0):
                final[gsn] = {
                    'gsn': gsn,
                    'index_name': key.split('|')[0],
                    'index_abbrev': key.split('|')[1],
                    'catalog_name': match_dict.get('catalog_name', ''),
                    'catalog_abbrev': match_dict.get('catalog_abbrev', ''),
                    'catalog_typ': match_dict.get('catalog_typ', ''),
                    'score': match_dict.get('score', 0),
                }
        else:
            final[gsn] = {
                'gsn': gsn,
                'index_name': key.split('|')[0],
                'index_abbrev': key.split('|')[1],
                'catalog_name': match_dict.get('catalog_name', ''),
                'catalog_abbrev': match_dict.get('catalog_abbrev', ''),
                'catalog_typ': match_dict.get('catalog_typ', ''),
                'score': match_dict.get('score', 0),
            }

    # Save final GSN list
    final_list = sorted(final.values(), key=lambda x: x['gsn'])
    out_path = base / 'data' / 'final_gsn_list.json'
    with open(out_path, 'w') as f:
        json.dump(
            {
                'total_matched': len(final_list),
                'still_unmatched': len(data['unmatched']),
                'still_ambiguous': len(still_amb),
                'laws': final_list,
                'unmatched': data['unmatched'],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\nFinal GSN list: {len(final_list)} unique GSNs")
    print(f"Saved to {out_path}")

    # Typ breakdown
    typs = {}
    for law in final_list:
        t = law['catalog_typ']
        typs[t] = typs.get(t, 0) + 1
    print(f"By type: {typs}")

    # Show laws ready for pipeline
    print(f"\nReady for pipeline: {len(final_list)} laws")
    for law in final_list[:20]:
        print(f"  GSN {law['gsn']}: {law['index_abbrev']} ({law['catalog_typ']}) - {law['index_name'][:60]}")


if __name__ == '__main__':
    main()

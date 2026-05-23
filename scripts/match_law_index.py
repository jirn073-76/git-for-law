"""Match law index entries to OGD GSNs via the law catalog."""

import json
import re
from difflib import SequenceMatcher
from pathlib import Path


def normalize(s):
    s = s.lower().strip()
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'\b(19|20)\d{2}\b', '', s)
    s = re.sub(r'[.,;:()\-/]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def name_similarity(a_norm, b_norm):
    """Jaccard + sequence ratio for normalized names."""
    if a_norm == b_norm:
        return 1.0
    a_words = set(a_norm.split())
    b_words = set(b_norm.split())
    if not a_words or not b_words:
        return 0.0
    overlap = len(a_words & b_words)
    jaccard = overlap / len(a_words | b_words)
    seq = SequenceMatcher(None, a_norm, b_norm).ratio()
    return 0.6 * jaccard + 0.4 * seq


def build_catalog_index(catalog):
    """Build fast lookup indices from catalog."""
    by_abbrev_exact = {}   # UPPERCASE abbrev -> [(gsn, info)]
    by_abbrev_norm = {}    # normalized abbrev -> [(gsn, info)]
    by_word = {}           # first significant word -> [(gsn, info, name_norm)]

    for gsn, info in catalog.items():
        abbrev = info.get('abbrev', '').strip()
        name = info.get('name', '').strip()

        if abbrev:
            by_abbrev_exact.setdefault(abbrev.upper(), []).append((gsn, info))
            a_norm = normalize(abbrev)
            if a_norm:
                by_abbrev_norm.setdefault(a_norm, []).append((gsn, info))

        if name:
            n_norm = normalize(name)
            if n_norm:
                words = n_norm.split()
                if words:
                    w = words[0]
                    by_word.setdefault(w, []).append((gsn, info, n_norm))

    return by_abbrev_exact, by_abbrev_norm, by_word


def match_index(index_entries, catalog, min_score=0.6):
    by_abbrev_exact, by_abbrev_norm, by_word = build_catalog_index(catalog)

    matched = {}
    unmatched = []
    ambiguous = {}

    for entry in index_entries:
        j_abbrev = entry['abbrev'].strip()
        j_name = entry['name'].strip()
        j_key = f"{j_name}|{j_abbrev}"

        best_score = 0.0
        best_matches = []

        # Step 1: Exact abbreviation lookup (O(1))
        exact_hits = by_abbrev_exact.get(j_abbrev.upper(), [])
        if exact_hits:
            best_score = 1.0
            best_matches = [(gsn, info, 1.0) for gsn, info in exact_hits]

        # Step 2: Normalized abbreviation lookup (O(1))
        if not best_matches:
            j_a_norm = normalize(j_abbrev)
            if j_a_norm and j_a_norm in by_abbrev_norm:
                best_matches = [(gsn, info, 0.95) for gsn, info in by_abbrev_norm[j_a_norm]]
                best_score = 0.95

        # Step 3: Fuzzy abbreviation matching on near misses
        if not best_matches and j_a_norm:
            candidates = []
            for norm_key, entries in by_abbrev_norm.items():
                if norm_key == j_a_norm:
                    candidates.extend((gsn, info, 1.0) for gsn, info in entries)
                elif j_a_norm in norm_key or norm_key in j_a_norm:
                    candidates.extend((gsn, info, 0.85) for gsn, info in entries)
            if candidates:
                best_score = 0.85
                best_matches = candidates

        # Step 4: Name matching using word index
        if not best_matches or best_score < 0.8:
            j_name_norm = normalize(j_name)
            j_words = j_name_norm.split()
            if j_words:
                # Determine Typ filter from abbreviation pattern
                # BG-type laws typically end in 'G' (Gesetz), V-type end in 'V' or 'VO'
                candidate_gsns = set()
                candidates = []

                # Look up by first word
                first_word = j_words[0]
                for w in [first_word] + (j_words[1:2] if len(j_words) > 1 else []):
                    if w in by_word:
                        for gsn, info, c_name_norm in by_word[w]:
                            if gsn in candidate_gsns:
                                continue
                            candidate_gsns.add(gsn)
                            score = name_similarity(j_name_norm, c_name_norm)
                            if score >= min_score and score > best_score - 0.1:
                                candidates.append((gsn, info, score))

                if candidates:
                    candidates.sort(key=lambda x: x[2], reverse=True)
                    best_score = candidates[0][2]
                    best_matches = [c for c in candidates if c[2] >= best_score - 0.02]

        if best_matches and best_score >= min_score:
            unique_matches = list({m[0]: m for m in best_matches}.values())
            if len(unique_matches) == 1:
                matched[j_key] = unique_matches[0]
            else:
                ambiguous[j_key] = unique_matches
        else:
            unmatched.append(entry)

    return matched, unmatched, ambiguous


def main():
    base = Path(__file__).resolve().parent.parent

    with open(base / 'data' / 'law_index_entries.json') as f:
        index_entries = json.load(f)
    with open(base / 'data' / 'law_catalog.json') as f:
        catalog = json.load(f)

    print(f"Index entries: {len(index_entries)}")
    print(f"Catalog entries: {len(catalog)}")

    matched, unmatched, ambiguous = match_index(index_entries, catalog)

    print(f"\nMatched: {len(matched)}")
    print(f"Ambiguous: {len(ambiguous)}")
    print(f"Unmatched: {len(unmatched)}")

    output = {
        "matched": {
            key: {
                "index_name": key.split("|")[0],
                "index_abbrev": key.split("|")[1],
                "gsn": gsn,
                "catalog_name": info.get("name", ""),
                "catalog_abbrev": info.get("abbrev", ""),
                "catalog_typ": info.get("typ", ""),
                "score": score,
            }
            for key, (gsn, info, score) in matched.items()
        },
        "ambiguous": {
            key: [
                {
                    "gsn": gsn,
                    "catalog_name": info.get("name", ""),
                    "catalog_abbrev": info.get("abbrev", ""),
                    "catalog_typ": info.get("typ", ""),
                    "score": score,
                }
                for gsn, info, score in matches
            ]
            for key, matches in ambiguous.items()
        },
        "unmatched": unmatched,
    }

    out_path = base / 'data' / 'law_index_matched.json'
    with open(out_path, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nSaved match results to {out_path}")

    if unmatched:
        print(f"\nFirst 30 unmatched:")
        for e in unmatched[:30]:
            print(f"  {e['abbrev']}: {e['name'][:80]}")

    if ambiguous:
        print(f"\nAmbiguous matches:")
        for key, matches in list(ambiguous.items())[:10]:
            print(f"  {key}:")
            for gsn, info, score in matches:
                print(f"    GSN {gsn}: {info.get('name','')} ({info.get('abbrev','')}) score={score:.2f}")


if __name__ == "__main__":
    main()

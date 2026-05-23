"""Comprehensive verification of all laws: section IDs, sr-only, ordering, fallbacks."""
import json, re
from pathlib import Path
from collections import Counter, defaultdict

LAWS_DIR = Path(__file__).resolve().parent.parent / "data" / "laws"

def sort_key_section(sid):
    """Sort key for mixed §/Art/Anlage section IDs."""
    m = re.match(r'(?:§|Art|Anlage)_(\d+)([a-z]?)', sid)
    if m:
        return (int(m.group(1)), m.group(2))
    return (999999, sid)

def check_ordering(ids, sections):
    """Check if sections of the same type are in order. Returns list of issues."""
    issues = []
    for prefix in ('§', 'Art', 'Anlage'):
        typed = [(i, sid) for i, sid in enumerate(ids) if sid.startswith(f'{prefix}_')]
        if len(typed) < 2:
            continue
        sorted_ids = sorted([sid for _, sid in typed], key=sort_key_section)
        actual = [sid for _, sid in typed]
        for j, (exp, act) in enumerate(zip(sorted_ids, actual)):
            if exp != act:
                # Check if the expected section exists elsewhere (reordering)
                # or is missing entirely (repealed section)
                exp_pos = actual.index(exp) if exp in actual else -1
                act_pos = sorted_ids.index(act) if act in sorted_ids else -1
                issues.append({
                    'prefix': prefix,
                    'position': typed[j][0],
                    'expected': exp,
                    'actual': act,
                    'expected_exists': exp in actual,
                    'type': 'reorder' if exp in actual else 'missing',
                })
                break  # First issue per prefix is enough
    return issues

def main():
    stats = Counter()
    sr_laws = []
    fallback_laws = []
    ordering_issues = []
    type_mix = defaultdict(list)

    for d in sorted(LAWS_DIR.iterdir()):
        if not d.is_dir():
            continue
        fjson = d / 'fassung.json'
        if not fjson.exists():
            continue
        stats['total'] += 1

        with open(fjson) as f:
            sections = json.load(f)

        if isinstance(sections, dict):
            ids = list(sections.keys())
        elif isinstance(sections, list):
            ids = [s.get('section_id', '') for s in sections]
        else:
            continue

        if not ids:
            stats['empty'] += 1
            continue

        # Section ID type counts
        proper = sum(1 for s in ids if not s.startswith('Section-'))
        pct = proper / len(ids) * 100

        sr_count = sum(1 for s in ids if 'römisch' in s.lower() or 'Artikel_' in s)
        fn_count = sum(1 for s in ids if s.startswith('Section-'))

        if sr_count:
            sr_laws.append((d.name, sr_count, len(ids)))
        if fn_count:
            fallback_laws.append((d.name, fn_count, len(ids), round(pct, 1)))
        if pct < 50:
            stats['low_proper'] += 1

        # Track section type distribution
        para = sum(1 for s in ids if s.startswith('§_'))
        art = sum(1 for s in ids if s.startswith('Art_'))
        anl = sum(1 for s in ids if s.startswith('Anlage_'))
        if (para > 0 and art > 0) or (para > 0 and anl > 0) or (art > 0 and anl > 0):
            type_mix['mixed'].append(d.name)

        # Ordering check
        issues = check_ordering(ids, sections)
        if issues:
            ordering_issues.append((d.name, issues, len(ids)))

    # Print results
    print(f"=== VERIFICATION REPORT ===")
    print(f"Total laws: {stats['total']}")
    print(f"Empty: {stats['empty']}")
    print(f"SR-only in IDs: {len(sr_laws)}")
    print(f"Section-N fallback: {len(fallback_laws)}")
    print(f"<50% proper: {stats['low_proper']}")
    print(f"Ordering issues: {len(ordering_issues)}")
    print()

    if sr_laws:
        sr_laws.sort(key=lambda x: -x[1])
        print(f"=== SR-ONLY LAWS ({len(sr_laws)}) ===")
        for name, cnt, total in sr_laws:
            print(f"  {name}: {cnt}/{total}")
        print()

    if fallback_laws:
        fallback_laws.sort(key=lambda x: -x[1])
        print(f"=== SECTION-N FALLBACK LAWS ({len(fallback_laws)}) ===")
        for name, cnt, total, pct in fallback_laws:
            print(f"  {name}: {cnt}/{total} ({pct}% proper)")
        print()

    if ordering_issues:
        print(f"=== ORDERING ISSUES ({len(ordering_issues)}) ===")
        real_issues = 0
        for name, issues, total in ordering_issues:
            real = [i for i in issues if i['type'] == 'reorder']
            missing = [i for i in issues if i['type'] == 'missing']
            if real:
                real_issues += 1
                for iss in real:
                    print(f"  {name} [{iss['prefix']}]: pos {iss['position']} got {iss['actual']} expected {iss['expected']} (REORDER, {total} total)")
            if missing:
                for iss in missing:
                    print(f"  {name} [{iss['prefix']}]: pos {iss['position']} got {iss['actual']} expected {iss['expected']} (MISSING, {total} total)")
        print()

    # Summary verdict
    ok = len(sr_laws) == 0 and len(fallback_laws) == 0 and real_issues == 0
    print(f"=== VERDICT ===")
    if ok:
        print("ALL CLEAN - no sr-only, no fallbacks, no real ordering issues")
    else:
        problems = []
        if sr_laws:
            problems.append(f"{len(sr_laws)} laws with sr-only IDs")
        if fallback_laws:
            problems.append(f"{len(fallback_laws)} laws with Section-N fallbacks")
        if real_issues > 0:
            problems.append(f"{real_issues} laws with real ordering issues")
        print(f"PROBLEMS: {', '.join(problems)}")

if __name__ == '__main__':
    main()

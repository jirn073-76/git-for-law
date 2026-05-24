"""FastAPI BFF for git-for-law-austria. Serves the frontend and a REST API.

Usage: python3 bff/server.py
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
LAWS_DIR = ROOT / "data" / "laws"
FRONTEND_DIR = ROOT / "frontend"

app = FastAPI(title="Git-for-Law Austria API")

# Map abbreviation → full law name from catalog
_name_cache = {}
_laws_cache = {"data": None, "ts": 0}
_changed_cache = {}  # abbrev -> {commit_hash: [section_ids]}
_LAWS_CACHE_TTL = 300


def _load_names():
    """Build abbrev→name mapping from law_catalog_merged.json + gsn_to_abbrev.json."""
    global _name_cache
    if _name_cache:
        return _name_cache
    gsn_to_abbrev_path = ROOT / "data" / "gsn_to_abbrev.json"
    catalog_path = ROOT / "data" / "law_catalog_merged.json"
    if not gsn_to_abbrev_path.exists() or not catalog_path.exists():
        return {}
    with open(gsn_to_abbrev_path) as f:
        gsn_to_abbrev = json.load(f)
    with open(catalog_path) as f:
        catalog = json.load(f)
    abbrev_to_gsn = {}
    for gsn, abbrev in gsn_to_abbrev.items():
        if abbrev not in abbrev_to_gsn:
            abbrev_to_gsn[abbrev] = gsn
    for abbrev, gsn in abbrev_to_gsn.items():
        entry = catalog.get(gsn, {})
        name = entry.get("name", "")
        if name:
            _name_cache[abbrev] = name
    # Fallback: direct catalog lookup by abbrev field for repos not in gsn_to_abbrev
    for gsn, entry in catalog.items():
        cat_abbrev = entry.get("abbrev", "")
        if cat_abbrev and cat_abbrev not in _name_cache:
            name = entry.get("name", "")
            if name:
                _name_cache[cat_abbrev] = name
    # Normalize names: strip numeric prefixes, replace NBSP with regular space
    import re as _re
    for abbrev, name in list(_name_cache.items()):
        name = name.replace('\xa0', ' ')
        name = _re.sub(r'^\d+\.\s+', '', name)
        _name_cache[abbrev] = name
    return _name_cache


_name_cache = _load_names()

# Load synthetic abbreviations (plausible Kurztitel for laws without official Abkürzung)
_synthetic_path = ROOT / "data" / "synthetic_abbrevs.json"
_synthetic_abbrevs = {}
if _synthetic_path.exists():
    with open(_synthetic_path) as f:
        _synthetic_abbrevs = json.load(f)


def _get_repo(abbrev: str) -> Path:
    path = LAWS_DIR / abbrev
    if not path.exists() or not (path / ".git").exists():
        raise HTTPException(404, f"Law '{abbrev}' not found")
    return path


def _load_fassung(repo: Path, commit_ref: str = "HEAD") -> dict:
    """Load fassung.json from a git commit ref."""
    try:
        output = subprocess.check_output(
            ["git", "-C", str(repo), "show", f"{commit_ref}:fassung.json"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
        return json.loads(output)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}


def _get_first_commit(repo: Path) -> str:
    """Return the hash of the first (oldest) commit — the Stammfassung."""
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-list", "--max-parents=0", "HEAD"],
            text=True, timeout=5,
        ).strip()
    except subprocess.CalledProcessError:
        return "HEAD"


# Cache: (repo_name, commit_ref) -> complete fassung dict
_complete_cache: dict[tuple[str, str], dict] = {}
_MAX_CACHE_SIZE = 256


def _norm_section_id(sid: str) -> str:
    """Normalize malformed section IDs like '§._10._Paragraph_10,' → '§_10'."""
    # Already clean compound key: Art_I_§_1 or Art_I_§_1_2 (dedup)
    if re.match(r"^Art_[^_]+_§_\d+[a-z]?(?:_\d+)?$", sid):
        return sid
    # Already clean: §_10, Art_5, Anlage_3
    if re.match(r"^(§|Art\.?|Anlage)_\d+[a-z]?(\))?$", sid):
        return sid
    # §._1._Paragraph_eins, → §_1  (parser used underscores instead of spaces)
    m = re.search(r"§\.?[_ ]?(\d+[a-z]?)\b", sid)
    if m:
        return f"§_{m.group(1)}"
    m = re.search(r"Art\.?[_ ]?(\d+[a-z]?)\b", sid)
    if m:
        return f"Art_{m.group(1)}"
    m = re.search(r"Anlage\.?[_ ]?(\d+[a-z]?)\b", sid)
    if m:
        return f"Anlage_{m.group(1)}"
    return sid


def _extract_id_from_body(body: str) -> str | None:
    """Try to extract a section/paragraph/article ID from body text.

    Patterns like '§. 1. Paragraph eins,' → '§_1', 'Art. 3.' → 'Art_3'.
    Searches the full body since IDs may follow structural headings.
    """
    if not body or len(body) < 5:
        return None
    # Art. I § 1. Paragraph eins, ... — compound article+paragraph (old RIS)
    m = re.search(
        r"Art\.?\s*([IVXLCDM]+[a-z]?|\d+[a-z]?)[\s,.]*"
        r"§\.?\s*(\d+[a-z]?)\s*[,.]?\s*(?:Paragraph|Absatz|Paragraf)",
        body,
    )
    if m:
        return f"Art_{m.group(1)}_§_{m.group(2)}"
    # §. 1. Paragraph eins, ... — classic old-RIS pattern (anywhere in body)
    m = re.search(r"§\.?\s*(\d+[a-z]?)\s*[,.]?\s*(?:Paragraph|Absatz|Paragraf)", body)
    if m:
        return f"§_{m.group(1)}"
    # Art. 1. ...
    m = re.search(r"Art\.?\s*(\d+[a-z]?)\s*[,.]?\s", body)
    if m:
        return f"Art_{m.group(1)}"
    # Anlage 1 ...
    m = re.search(r"Anlage\s+(\d+[a-z]?)", body)
    if m:
        return f"Anlage_{m.group(1)}"
    # § 1. ... or § 1 (1) ... (at start or after newline)
    m = re.search(r"(?:^|\n)\s*(?:§|Paragraf|Paragraph|Paragraphen)\s+(\d+[a-z]?)\s*[.,)\s]", body)
    if m:
        return f"§_{m.group(1)}"
    # Fallback: lone "§ 1" anywhere, but only if body is short (to avoid false matches)
    if len(body) < 300:
        m = re.search(r"§\s+(\d+[a-z]?)(?:\s|$)", body)
        if m:
            return f"§_{m.group(1)}"
    return None


def _norm_keys(d: dict) -> dict:
    """Return a copy of d with section IDs normalized, extracting IDs from body for Section-N."""
    result = {}
    text_entries = []  # Saved as fallback if everything else gets filtered out
    for k, v in d.items():
        if k == "Text":
            if isinstance(v, dict) and len(v.get("body", "")) > 100:
                text_entries.append(("Text", dict(v)))
            continue  # Skip "Text" entries that have no real identifier
        nk = _norm_section_id(k)
        if isinstance(v, dict):
            v = dict(v)
            raw_sid = v.get("section_id", nk)
            if raw_sid == "Text":
                # Try to extract from body, otherwise skip
                extracted = _extract_id_from_body(v.get("body", ""))
                if extracted:
                    v["section_id"] = extracted
                    nk = extracted
                else:
                    continue  # Skip unrecoverable "Text" entries
            else:
                v["section_id"] = _norm_section_id(raw_sid)
                nk = v["section_id"]
            # For Section-N entries, try to extract a proper ID from the body
            if nk.startswith("Section-"):
                extracted = _extract_id_from_body(v.get("body", ""))
                if extracted:
                    nk = extracted
                    v["section_id"] = extracted
        if nk in result:
            existing = result[nk].get("body", "") if isinstance(result[nk], dict) else ""
            new_body = v.get("body", "") if isinstance(v, dict) else ""
            if len(new_body) > len(existing):
                result[nk] = v
        else:
            result[nk] = v
    if not result and text_entries:
        for i, (_k, v) in enumerate(text_entries, 1):
            extracted = _extract_id_from_body(v.get("body", ""))
            new_key = extracted or f"Section-{i}"
            v["section_id"] = new_key
            result[new_key] = v
    return result


def _load_complete_fassung(repo: Path, commit_ref: str = "HEAD") -> dict:
    """Return the full law at commit_ref, overlaying sparse versions onto StF.

    Version commits store only amended sections. If the version is sparse and its
    section IDs overlap with StF (no renumbering), we reconstruct by walking git
    history from StF to commit_ref, accumulating changes via dict overlay.
    When StF itself has poor data (many Section-N), we find the best baseline commit.
    """
    abbrev = repo.name
    cache_key = (abbrev, commit_ref)
    if cache_key in _complete_cache:
        return dict(_complete_cache[cache_key])

    direct = _load_fassung(repo, commit_ref)
    direct = _norm_keys(direct)
    stf_hash = _get_first_commit(repo)
    stf = _norm_keys(_load_fassung(repo, stf_hash))

    if not stf:
        result = direct or {}
        _complete_cache[cache_key] = result
        return dict(result)

    if commit_ref == stf_hash:
        _complete_cache[cache_key] = stf
        return dict(stf)

    def _proper_keys(d: dict) -> set[str]:
        return {k for k in d if not k.startswith("Section-") and k != "Text"}

    stf_proper = _proper_keys(stf)
    direct_proper = _proper_keys(direct)

    # If the version already has plenty of sections, use it as-is
    if len(direct_proper) >= 30:
        _complete_cache[cache_key] = direct
        return dict(direct)

    # If version has some sections but numbering doesn't match StF (renumbered law),
    # overlay would corrupt the data. If StF has many Section-N (parser failures),
    # return StF for more complete content. Otherwise return direct.
    if len(direct_proper) >= 5:
        overlap = len(direct_proper & stf_proper)
        if overlap < len(direct_proper) * 0.4:
            stf_sn = len([k for k in stf if k.startswith("Section-")])
            if stf_sn > len(stf) * 0.2 and len(stf) > len(direct):
                # StF has parser failures but more content — prefer StF
                result = stf
            else:
                result = direct
            _complete_cache[cache_key] = result
            return dict(result)

    try:
        target_hash = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", commit_ref],
            text=True, timeout=5,
        ).strip()
    except subprocess.CalledProcessError:
        target_hash = commit_ref

    if target_hash == stf_hash:
        _complete_cache[cache_key] = stf
        return dict(stf)

    # Find the best baseline: if StF is poor, scan commits for a better one
    baseline = dict(stf)
    stf_sn_ratio = len([k for k in stf if k.startswith("Section-")]) / max(len(stf), 1)
    if len(stf_proper) < 20 or stf_sn_ratio > 0.4:
        try:
            all_hashes = subprocess.check_output(
                ["git", "-C", str(repo), "rev-list", "--reverse", f"{stf_hash}..{target_hash}"],
                text=True, timeout=10,
            ).strip().split("\n")
            best_count = len(stf_proper)
            for h in all_hashes:
                if not h:
                    continue
                candidate = _norm_keys(_load_fassung(repo, h))
                cp = len(_proper_keys(candidate))
                if cp > best_count * 1.3 and cp >= 10:
                    baseline = dict(candidate)
                    best_count = cp
        except subprocess.CalledProcessError:
            pass

    # Walk history from best baseline to target, overlaying changes
    try:
        revs = subprocess.check_output(
            ["git", "-C", str(repo), "rev-list", "--reverse", f"{stf_hash}..{target_hash}"],
            text=True, timeout=10,
        ).strip().split("\n")
    except subprocess.CalledProcessError:
        revs = []

    merged = dict(baseline)
    for rev in revs:
        if not rev:
            continue
        version_sections = _norm_keys(_load_fassung(repo, rev))
        if version_sections:
            merged.update(version_sections)

    merged.pop("Text", None)

    if len(_complete_cache) >= _MAX_CACHE_SIZE:
        _complete_cache.pop(next(iter(_complete_cache)))
    _complete_cache[cache_key] = merged
    return dict(merged)


def _git_log(repo: Path) -> list:
    """Return list of (hash, full_message) for all commits (newest first)."""
    try:
        output = subprocess.check_output(
            ["git", "-C", str(repo), "log", "--format=%H %s"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except subprocess.CalledProcessError:
        return []
    commits = []
    for line in output.strip().split("\n"):
        if line:
            parts = line.split(" ", 1)
            commits.append((parts[0], parts[1] if len(parts) > 1 else ""))
    return commits


def _parse_commit_message(msg: str) -> Optional[dict]:
    m = re.match(r"^(.+?)\s+\[([^\]]+)\]:\s*(.*)", msg)
    if m:
        return {"abbrev": m.group(1), "fassung_vom": m.group(2), "aenderung": m.group(3).strip()}
    return None


def _roman_to_int(s: str) -> int | None:
    """Convert Roman numeral string to int, or None if not valid."""
    try:
        result = 0
        values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
        prev = 0
        for c in reversed(s.upper()):
            v = values.get(c)
            if v is None:
                return None
            if v >= prev:
                result += v
            else:
                result -= v
            prev = v
        return result
    except (KeyError, ValueError):
        return None


def _section_sort_key(sid: str):
    """Natural sort: § sections first, then Art (Arabic before Roman), then Anlage, then fallbacks.

    Compound keys like Art_I_§_1 sort by article number first, then paragraph number.
    """
    _PREFIX_ORDER = {"§": 0, "Art": 1, "Anlage": 2}
    m_compound = re.match(r"Art_([^_]+)_§_(\d+)([a-z]?)(?:_(\d+))?$", sid)
    if m_compound:
        art_part = m_compound.group(1)
        par_num = int(m_compound.group(2))
        par_suffix = m_compound.group(3)
        dedup = int(m_compound.group(4) or 0)
        roman_val = _roman_to_int(art_part)
        if roman_val is not None:
            return (1, 1, roman_val, "", 0, par_num, par_suffix, dedup)
        art_m = re.match(r"(\d+)([a-z]?)$", art_part)
        if art_m:
            return (1, 0, int(art_m.group(1)), art_m.group(2), 0, par_num, par_suffix, dedup)
        return (1, 0, 0, art_part, 0, par_num, par_suffix, dedup)
    parts = sid.split("_", 1)
    if len(parts) == 2:
        prefix, numpart = parts
        pf_rank = _PREFIX_ORDER.get(prefix, 3)
        m = re.match(r"(\d+)([a-z]?)(?:_(\d+))?$", numpart)
        if m:
            dedup = int(m.group(3) or 0)
            return (pf_rank, 0, int(m.group(1)), m.group(2), dedup)
        roman_val = _roman_to_int(numpart)
        if roman_val is not None:
            return (pf_rank, 1, roman_val, "")
        sm = re.match(r"Section-(\d+)", sid)
        if sm:
            return (pf_rank, 0, int(sm.group(1)), "")
        return (pf_rank, 0, 0, sid)
    sm = re.match(r"Section-(\d+)", sid)
    if sm:
        return (3, 0, int(sm.group(1)), "")
    return (4, 0, 0, sid)


def _changed_sections(repo: Path, commit_hash: str) -> list:
    """Return list of section IDs whose body changed in a commit vs its parent."""
    try:
        output = subprocess.check_output(
            ["git", "-C", str(repo), "diff", "-U4", f"{commit_hash}^", commit_hash, "--", "fassung.json"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except subprocess.CalledProcessError:
        try:
            output = subprocess.check_output(
                ["git", "-C", str(repo), "diff-tree", "--no-commit-id", "--name-only", "-r", commit_hash],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
            files = [f.strip() for f in output.strip().split("\n") if f.strip().endswith(".json")]
            return [f.replace(".json", "") for f in files]
        except subprocess.CalledProcessError:
            return []

    changed = set()
    current_section = None
    for line in output.split("\n"):
        m = re.match(r'^[+-]\s+"body":', line)
        if m:
            if current_section:
                changed.add(current_section)
        else:
            m = re.match(r'^\s+"(§_\w+|Art_[\w§]+|Anlage_\w+)"', line)
            if m:
                current_section = m.group(1)
    return sorted(changed, key=_section_sort_key)


def _git_count_sections(repo: Path, commit_hash: str) -> int:
    """Count sections in the complete fassung at HEAD (includes all amendment overlays)."""
    fassung = _load_complete_fassung(repo)
    return len(fassung)


def _load_laws_index():
    """Load pre-computed law list from JSON, or scan repos as fallback."""
    index_path = ROOT / "data" / "laws_index.json"
    if index_path.exists():
        with open(index_path) as f:
            return json.load(f)

    laws = []
    for d in sorted(LAWS_DIR.iterdir()):
        if not d.is_dir() or not (d / ".git").exists():
            continue
        abbrev = d.name
        repo = d
        commits = _git_log(repo)
        newest = None
        if commits:
            dates = [d for _, msg in commits
                     if (d := (_parse_commit_message(msg) or {}).get("fassung_vom"))]
            if dates:
                newest = max(dates)
        sections = _git_count_sections(repo, commits[0][0]) if commits else 0
        laws.append({
            "abbrev": abbrev,
            "name": _name_cache.get(abbrev, ""),
            "versions": len(commits),
            "sections": sections,
            "newest_fassung_vom": newest,
            "synthetic": abbrev in _synthetic_abbrevs,
        })
    return laws


@app.get("/api/laws")
def list_laws(q: Optional[str] = Query(None)):
    global _laws_cache
    now = time.monotonic()
    if _laws_cache["data"] is not None and (now - _laws_cache["ts"]) < _LAWS_CACHE_TTL:
        laws = _laws_cache["data"]
    else:
        laws = _load_laws_index()
        _laws_cache = {"data": laws, "ts": now}

    if q:
        ql = q.lower()
        return [l for l in laws if ql in l["abbrev"].lower() or ql in l.get("name", "").lower()]
    return laws


@app.get("/api/laws/{abbrev}")
def get_law(abbrev: str):
    """Get detail for a specific law including version list."""
    repo = _get_repo(abbrev)
    commits = _git_log(repo)

    fassung = _load_complete_fassung(repo)
    total_sections = len(fassung)

    cache = _changed_cache.setdefault(abbrev, {})

    versions = []
    seen_dates = set()
    for h, msg in commits:
        info = _parse_commit_message(msg)
        if info and info["fassung_vom"] not in seen_dates:
            seen_dates.add(info["fassung_vom"])
            if h not in cache:
                cache[h] = _changed_sections(repo, h)
            changed = cache[h]
            versions.append({
                "fassung_vom": info["fassung_vom"],
                "aenderung": info["aenderung"],
                "commit_hash": h[:8],
                "sections": None,
                "changed_count": len(changed),
                "changed_sections": changed[:30],
            })

    versions.sort(key=lambda v: v["fassung_vom"], reverse=True)

    # Fill section count for the chronologically newest version
    if versions:
        versions[0]["sections"] = total_sections

    return {
        "abbrev": abbrev,
        "name": _name_cache.get(abbrev, ""),
        "versions_count": len(versions),
        "sections": total_sections,
        "versions_list": versions,
        "synthetic": abbrev in _synthetic_abbrevs,
    }


@app.get("/api/laws/{abbrev}/versions")
def list_versions(abbrev: str):
    """List all versions (fassung_vom dates) for a law."""
    repo = _get_repo(abbrev)
    commits = _git_log(repo)
    return [
        v for v in [
            _parse_commit_message(msg) for _, msg in commits
        ] if v
    ]


@app.get("/api/laws/{abbrev}/sections")
def get_sections(abbrev: str, date: str = Query(...)):
    """Get sections for a law at a specific fassung_vom date."""
    repo = _get_repo(abbrev)
    commits = _git_log(repo)
    commit_hash = None
    for h, msg in commits:
        info = _parse_commit_message(msg)
        if info and info["fassung_vom"] == date:
            commit_hash = h
            break
    if not commit_hash:
        raise HTTPException(404, f"Version '{date}' not found for '{abbrev}'")

    fassung = _load_complete_fassung(repo, commit_hash)
    sections = []
    for sid in sorted(fassung, key=_section_sort_key):
        sec = fassung[sid]
        sections.append({
            "section_id": sec.get("section_id", sid),
            "heading": _display_heading(sid, sec.get("heading", "")),
            "body": _strip_section_prefix(sec.get("body", "")),
            "section_type": sec.get("section_type", ""),
        })
    return sections


def _heading_is_bad(heading: str) -> bool:
    """True if heading is empty, annotation, or placeholder text."""
    return (
        not heading
        or heading == "Text"
        or heading == "Beachte für folgende Bestimmung"
        or heading.startswith("(Anm")
    )


def _display_heading(section_id: str, heading: str) -> str:
    """Return a clean display heading with section prefix, falling back to section_id if heading is annotation/empty."""
    prefix = _section_id_to_display(section_id, "")
    if heading and not _heading_is_bad(heading):
        if heading.lstrip().startswith(prefix.rstrip(".")):
            return heading
        return f"{prefix} {heading}"
    return prefix


def _strip_section_prefix(body: str) -> str:
    """Remove leading § N. or Art. N § M. from body — heading already shows the identifier."""
    m = re.match(r'^(?:Art(?:ikel)?\.?\s*(?:[IVXLCDM]+|\d+)\s*)?§\s*\d+[a-z]?\.\s+', body)
    if m:
        return body[m.end():]
    m = re.match(r'^Art(?:ikel)?\.?\s*[IVXLCDM]+\s+', body)
    if m:
        return body[m.end():]
    return body


def _section_id_to_display(section_id: str, fallback: str = "") -> str:
    """Generate display heading from section_id."""
    m = re.match(r"^Art_([^_]+)_§_(\d+[a-z]?)(?:_\d+)?$", section_id)
    if m:
        return f"Art. {m.group(1)} § {m.group(2)}."
    m = re.match(r"^§_(\d+[a-z]?)(?:_\d+)?$", section_id)
    if m:
        return f"§ {m.group(1)}."
    m = re.match(r"^Art_([^_]+)(?:_\d+)?$", section_id)
    if m:
        return f"Art. {m.group(1)}"
    if section_id.startswith("Anlage_"):
        return section_id.replace("Anlage_", "Anlage ")
    if section_id.startswith("Section-"):
        return fallback if (fallback and not _heading_is_bad(fallback)) else section_id
    cleaned = section_id.replace("_", " ")
    if cleaned in {"Beachte für folgende Bestimmung"}:
        return "Abschnitt"
    if len(cleaned) > 80:
        cleaned = cleaned[:77] + "..."
    return cleaned


@app.get("/api/laws/{abbrev}/diff")
def diff_versions(
    abbrev: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    """Diff two versions of a law."""
    repo = _get_repo(abbrev)
    commits = _git_log(repo)

    from_hash = to_hash = None
    for h, msg in commits:
        info = _parse_commit_message(msg)
        if info:
            if info["fassung_vom"] == from_date:
                from_hash = h
            if info["fassung_vom"] == to_date:
                to_hash = h

    if not from_hash:
        raise HTTPException(404, f"Version '{from_date}' not found")
    if not to_hash:
        raise HTTPException(404, f"Version '{to_date}' not found")
    if from_date == to_date:
        return {
            "law_abbrev": abbrev,
            "from_date": from_date,
            "to_date": to_date,
            "changed_sections": [],
            "unchanged_sections": [],
        }

    old = _load_complete_fassung(repo, from_hash)
    new = _load_complete_fassung(repo, to_hash)

    all_ids = set(old.keys()) | set(new.keys())
    changed = []
    unchanged = []

    for sid in sorted(all_ids, key=_section_sort_key):
        old_sec = old.get(sid, {})
        new_sec = new.get(sid, {})
        old_body = old_sec.get("body", "")
        new_body = new_sec.get("body", "")

        if old_body != new_body:
            changed.append({
                "section_id": sid,
                "heading": _display_heading(
                    sid,
                    new_sec.get("heading") or old_sec.get("heading") or ""
                ),
                "old_body": _strip_section_prefix(old_body) if old_body else None,
                "new_body": _strip_section_prefix(new_body) if new_body else None,
            })
        else:
            unchanged.append({
                "section_id": sid,
                "heading": _display_heading(sid, new_sec.get("heading") or ""),
            })

    return {
        "law_abbrev": abbrev,
        "from_date": from_date,
        "to_date": to_date,
        "changed_sections": changed,
        "unchanged_sections": unchanged,
    }


# Serve frontend static files (manual — avoid StaticFiles caching issues)
@app.get("/{rest:path}")
async def serve_frontend(rest: str):
    if rest.startswith("api/"):
        raise HTTPException(404)
    safe = rest.lstrip("/")
    file_path = FRONTEND_DIR / (safe or "index.html")
    if file_path.is_file() and file_path.resolve().is_relative_to(FRONTEND_DIR.resolve()):
        return FileResponse(file_path)
    # SPA fallback
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    raise HTTPException(404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8081)

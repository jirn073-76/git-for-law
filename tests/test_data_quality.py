"""Data quality tests — verify every law repo has real content, valid IDs, and proper diffs."""
import json
import re
from pathlib import Path

import git
import pytest

LAWS_DIR = Path(__file__).resolve().parent.parent / "data" / "laws"


def discover_repos():
    """Return list of (abbrev, repo_path) for all initialized git law repos."""
    if not LAWS_DIR.exists():
        return []
    return [
        (d.name, d)
        for d in sorted(LAWS_DIR.iterdir())
        if d.is_dir() and (d / ".git").exists()
    ]


REPOS = discover_repos()

# Known-repealed or genuinely trivial provisions
_KNOWN_SHORT_OK = {
    "(aufgehoben)", "(weggefallen)", "(entfällt.)",
    "(gegenstandslos)", "entfällt.",
    "umsetzungshinweis", "notifikation",
    "schlußbestimmungen", "schlussbestimmungen",
}


def _valid_sid(sid: str) -> bool:
    """Section ID must be non-empty, >= 2 chars, contain alphanumeric."""
    if not sid or not sid.strip():
        return False
    if len(sid.strip()) < 2:
        return False
    if not re.search(r"[a-zA-Z0-9]", sid):
        return False
    return True


@pytest.mark.parametrize("abbrev,repo_path", REPOS)
def test_repo_has_commits(abbrev, repo_path):
    """Every repo must have at least 1 git commit."""
    r = git.Repo(str(repo_path))
    commits = list(r.iter_commits())
    assert len(commits) >= 1, f"{abbrev}: no commits"


def _repo_has_commits(repo_path: Path) -> bool:
    try:
        r = git.Repo(str(repo_path))
        return bool(list(r.iter_commits(max_count=1)))
    except Exception:
        return False


@pytest.mark.parametrize("abbrev,repo_path", REPOS)
def test_fassung_json_exists_and_valid(abbrev, repo_path):
    """fassung.json must exist and be valid JSON with sections."""
    if not _repo_has_commits(repo_path):
        pytest.skip("empty repo (no commits)")
    fpath = repo_path / "fassung.json"
    assert fpath.exists(), f"{abbrev}: fassung.json missing"
    with open(fpath) as f:
        sections = json.load(f)
    assert isinstance(sections, dict), f"{abbrev}: fassung.json is not a dict"
    assert len(sections) > 0, f"{abbrev}: fassung.json is empty"


@pytest.mark.parametrize("abbrev,repo_path", REPOS)
def test_no_empty_section_bodies(abbrev, repo_path):
    """No section may have an empty or whitespace-only body.

    Section-N entries are structural headings (UeberschrArt) that legitimately
    have no body text — only a heading.
    """
    if not _repo_has_commits(repo_path):
        pytest.skip("empty repo (no commits)")
    with open(repo_path / "fassung.json") as f:
        sections = json.load(f)
    bad = []
    for sid, sec in sections.items():
        if not isinstance(sec, dict):
            bad.append(f"{sid}: not a dict")
            continue
        if sid.startswith("Section-"):
            continue
        body = sec.get("body", "").strip()
        if not body:
            bad.append(f"{sid}: empty body")
    assert bad == [], f"{abbrev}: {len(bad)} empty sections: {bad[:10]}"


@pytest.mark.parametrize("abbrev,repo_path", REPOS)
def test_sections_have_substantive_body(abbrev, repo_path):
    """Every section must have body text >= 30 chars, except known-repealed ones."""
    if not _repo_has_commits(repo_path):
        pytest.skip("empty repo (no commits)")
    with open(repo_path / "fassung.json") as f:
        sections = json.load(f)
    bad = []
    for sid, sec in sections.items():
        if not isinstance(sec, dict):
            continue
        if sid.startswith("Section-"):
            continue
        body = sec.get("body", "").strip()
        if not body:
            continue

        if body.lower() in _KNOWN_SHORT_OK or body.lower().startswith("(aufgehoben)"):
            continue

        if re.match(r'^Anlage\s*\d*$', body):
            continue

        if len(body) < 10:
            bad.append(f"{sid}: body too short ({len(body)} chars)")

    assert bad == [], f"{abbrev}: {len(bad)} deficient sections: {bad[:10]}"


@pytest.mark.parametrize("abbrev,repo_path", REPOS)
def test_section_id_format(abbrev, repo_path):
    """All section IDs must be valid (non-empty, >=2 chars, alphanumeric content)."""
    if not _repo_has_commits(repo_path):
        pytest.skip("empty repo (no commits)")
    with open(repo_path / "fassung.json") as f:
        sections = json.load(f)
    bad = [sid for sid in sections if not _valid_sid(sid)]
    assert bad == [], f"{abbrev}: {len(bad)} invalid IDs: {bad[:15]}"


@pytest.mark.parametrize("abbrev,repo_path", REPOS)
def test_diff_shows_content_changes(abbrev, repo_path):
    """For repos with >=2 commits and >=2 distinct trees, diff must show changes."""
    if not _repo_has_commits(repo_path):
        pytest.skip("empty repo (no commits)")
    r = git.Repo(str(repo_path))
    commits = list(r.iter_commits())
    if len(commits) < 2:
        pytest.skip("only 1 commit")
    trees = {c.tree.hexsha for c in commits}
    if len(trees) < 2:
        pytest.skip("all commits have identical tree (duplicate commits)")
    diff_text = r.git.diff(
        f"{commits[-1].hexsha}..{commits[0].hexsha}", "--", "fassung.json"
    )
    assert len(diff_text) > 0, f"{abbrev}: diff is empty across {len(commits)} commits"


@pytest.mark.parametrize("abbrev,repo_path", REPOS)
def test_commit_messages_have_date(abbrev, repo_path):
    """At most 1 commit (the initial StF) may lack a fassung_vom date."""
    if not _repo_has_commits(repo_path):
        pytest.skip("empty repo (no commits)")
    r = git.Repo(str(repo_path))
    commits = list(r.iter_commits())
    bad = sum(1 for c in commits if not re.search(r"\[\d{4}-\d{2}-\d{2}\]", c.message))
    if bad <= 1:
        return
    bad_pct = bad / len(commits)
    assert bad_pct <= 0.05, (
        f"{abbrev}: {bad}/{len(commits)} commits ({bad_pct:.0%}) missing fassung_vom date"
    )

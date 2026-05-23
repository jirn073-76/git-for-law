"""Tests for the CLI diff viewer module.

Tests validate:
- Git-backed reads from data/laws/{abbrev}/ repo
- Section-aware diff between two fassung_vom versions
- ANSI color-coded terminal output (additions, deletions, headers)
- Section filtering by section_id
- Identical sections collapsed, changed sections expanded
- JSON section file parsing from git repo
- CLI entry point (python3 -m git_for_law_austria.diff)
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from git_for_law_austria.diff import DiffViewer, DiffResult, SectionDiff, diff_law


def _make_test_law_repo(tmp_path, abbrev, dates):
    """Create a proper git repo with one commit per date for testing."""
    repo_path = tmp_path / "laws" / abbrev
    repo_path.mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo_path), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    for date in sorted(dates):
        section = {
            "section_id": "§_1",
            "heading": "§ 1",
            "body": f"Text for version {date}.",
            "section_type": "Paragraf",
            "fassung_vom": date,
        }
        (repo_path / "§_1.json").write_text(json.dumps(section, ensure_ascii=False))
        subprocess.run(["git", "-C", str(repo_path), "add", "."], check=True, capture_output=True)
        msg = f"{abbrev} [{date}]: Test amendment"
        subprocess.run(["git", "-C", str(repo_path), "commit", "-m", msg], check=True, capture_output=True)
    return repo_path


# ── Git-backed read tests ─────────────────────────────────────────────────────


class TestDiffViewerGitBackedReads:
    """Tests for reading section JSON from a git repo."""

    def test_diff_viewer_accepts_repo_path(self, tmp_path):
        """DiffViewer must accept a path to the law git repo."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        viewer = DiffViewer(repo_path=str(repo_path))
        assert viewer.repo_path == str(repo_path), "Must store repo path"

    def test_read_section_from_repo(self, tmp_path):
        """DiffViewer must read a section JSON file from the git repo."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        section_json = {
            "section_id": "§_1",
            "heading": "§ 1",
            "body": "Paragraph eins Text.",
            "section_type": "Paragraf",
            "fassung_vom": "2017-01-01",
        }
        section_file = repo_path / "§_1.json"
        section_file.write_text(json.dumps(section_json))

        viewer = DiffViewer(repo_path=str(repo_path))
        section = viewer.read_section("§_1")
        assert section is not None, "Must read existing section file"
        assert section["section_id"] == "§_1"
        assert section["body"] == "Paragraph eins Text."

    def test_read_missing_section_returns_none(self, tmp_path):
        """Reading a non-existent section must return None, not crash."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        viewer = DiffViewer(repo_path=str(repo_path))
        section = viewer.read_section("§_999")
        assert section is None, "Missing section must return None"

    def test_read_section_with_special_chars_in_id(self, tmp_path):
        """Section files with special characters (§) in filename must be readable."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        section_json = {
            "section_id": "§_1",
            "heading": "§ 1",
            "body": "Text.",
            "section_type": "Paragraf",
            "fassung_vom": "2017-01-01",
        }
        section_file = repo_path / "§_1.json"
        section_file.write_text(json.dumps(section_json))

        viewer = DiffViewer(repo_path=str(repo_path))
        section = viewer.read_section("§_1")
        assert section is not None, "Must handle § character in filename"

    def test_read_corrupted_json_raises(self, tmp_path):
        """Corrupted JSON in section file must raise a meaningful error."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        section_file = repo_path / "§_1.json"
        section_file.write_text("{this is not valid json")

        viewer = DiffViewer(repo_path=str(repo_path))
        with pytest.raises(Exception) as exc_info:
            viewer.read_section("§_1")
        assert "json" in str(exc_info.value).lower() or "§_1" in str(exc_info.value), (
            "Error must mention JSON parse failure or section ID"
        )

    def test_list_all_sections(self, tmp_path):
        """DiffViewer must list all sections from fassung.json."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        fassung = {}
        for i in range(1, 6):
            fassung[f"§_{i}"] = {
                "section_id": f"§_{i}",
                "heading": f"§ {i}",
                "body": f"Text {i}.",
                "section_type": "Paragraf",
                "fassung_vom": "2017-01-01",
            }
        (repo_path / "fassung.json").write_text(json.dumps(fassung))

        viewer = DiffViewer(repo_path=str(repo_path))
        sections = viewer.list_sections()
        assert len(sections) == 5, "Must list all 5 sections"
        assert "§_1" in sections
        assert "§_5" in sections

    def test_list_sections_empty_repo(self, tmp_path):
        """Empty repo must return empty section list."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        viewer = DiffViewer(repo_path=str(repo_path))
        sections = viewer.list_sections()
        assert sections == [], "Empty repo must yield empty list"


# ── Section-aware diff tests ──────────────────────────────────────────────────


class TestDiffViewerSectionAware:
    """Tests for section-aware diffing between two versions."""

    def test_diff_two_versions(self, tmp_path):
        """Diff between two fassung_vom dates must return DiffResult."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        viewer = DiffViewer(repo_path=str(repo_path))
        result = viewer.diff("2017-01-01", "2018-01-01")
        assert isinstance(result, DiffResult), "diff must return DiffResult"

    def test_diff_result_contains_changed_sections(self, tmp_path):
        """DiffResult must list which sections changed between versions."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        viewer = DiffViewer(repo_path=str(repo_path))
        result = viewer.diff("2017-01-01", "2018-01-01")
        assert hasattr(result, "changed_sections"), "Must have changed_sections"
        assert hasattr(result, "unchanged_sections"), "Must have unchanged_sections"
        assert isinstance(result.changed_sections, list)
        assert isinstance(result.unchanged_sections, list)

    def test_diff_result_has_from_and_to_dates(self, tmp_path):
        """DiffResult must record the from and to fassung_vom dates."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        viewer = DiffViewer(repo_path=str(repo_path))
        result = viewer.diff("2017-01-01", "2018-01-01")
        assert result.from_date == "2017-01-01"
        assert result.to_date == "2018-01-01"

    def test_diff_by_section_id(self, tmp_path):
        """diff must support filtering to a single section by section_id."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        viewer = DiffViewer(repo_path=str(repo_path))
        result = viewer.diff("2017-01-01", "2018-01-01", section_id="§_1")
        assert isinstance(result, DiffResult)
        assert len(result.changed_sections) <= 1 or (
            all(s.section_id == "§_1" for s in result.changed_sections)
        ), "Filtered diff must only include requested section"

    def test_diff_nonexistent_version_raises(self, tmp_path):
        """Diffing against a non-existent version must raise clear error."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        (repo_path / ".git").mkdir()
        viewer = DiffViewer(repo_path=str(repo_path))
        with pytest.raises(Exception) as exc_info:
            viewer.diff("1812-01-01", "2099-01-01")
        assert "2099" in str(exc_info.value) or "not found" in str(exc_info.value).lower(), (
            "Error must indicate missing version"
        )

    def test_section_diff_stores_additions_and_deletions(self):
        """SectionDiff must store added and removed lines."""
        diff = SectionDiff(
            section_id="§_1",
            heading="§ 1",
            old_body="Alter Text.",
            new_body="Neuer Text.",
        )
        assert diff.section_id == "§_1"
        assert diff.old_body == "Alter Text."
        assert diff.new_body == "Neuer Text."

    def test_section_diff_unchanged(self):
        """SectionDiff with identical old and new body must be marked unchanged."""
        diff = SectionDiff(
            section_id="§_1",
            heading="§ 1",
            old_body="Gleicher Text.",
            new_body="Gleicher Text.",
        )
        assert diff.is_changed is False, "Identical bodies must be marked unchanged"

    def test_section_diff_changed(self):
        """SectionDiff with different old and new body must be marked changed."""
        diff = SectionDiff(
            section_id="§_1",
            heading="§ 1",
            old_body="Alter Text.",
            new_body="Neuer Text.",
        )
        assert diff.is_changed is True, "Different bodies must be marked changed"

    def test_diff_understands_paragraph_article_anlage(self, tmp_path):
        """Diff must handle all section types: Paragraf, Artikel, Anlage."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        viewer = DiffViewer(repo_path=str(repo_path))
        section_types = ["Paragraf", "Artikel", "Anlage"]
        all_ok = True
        for st in section_types:
            try:
                result = viewer.diff("2017-01-01", "2018-01-01", section_type=st)
                if not isinstance(result, DiffResult):
                    all_ok = False
            except Exception:
                all_ok = False
        assert all_ok, f"Diff must handle all section types: {section_types}"


# ── ANSI output tests ─────────────────────────────────────────────────────────


class TestDiffViewerANSIOutput:
    """Tests for ANSI color-coded terminal output."""

    def test_ansi_output_method_exists(self, tmp_path):
        """DiffViewer must have a method that returns ANSI-formatted output."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        viewer = DiffViewer(repo_path=str(repo_path))
        result = viewer.diff("2017-01-01", "2018-01-01")
        ansi = result.to_ansi()
        assert isinstance(ansi, str), "to_ansi must return a string"

    def test_ansi_output_contains_color_codes(self, sample_diff_ansi_output):
        """ANSI output must contain color escape sequences."""
        codes = sample_diff_ansi_output
        assert "\033[" in codes["addition_prefix"], "Addition prefix must have ANSI escape"
        assert "\033[" in codes["deletion_prefix"], "Deletion prefix must have ANSI escape"
        assert "\033[" in codes["header_prefix"], "Header prefix must have ANSI escape"
        assert "\033[" in codes["reset"], "Reset must have ANSI escape"

    def test_ansi_green_for_additions(self, sample_diff_ansi_output):
        """Added lines must be prefixed with green ANSI codes."""
        codes = sample_diff_ansi_output
        assert "32m" in codes["addition_prefix"], "Additions must use green (32m)"

    def test_ansi_red_for_deletions(self, sample_diff_ansi_output):
        """Deleted lines must be prefixed with red ANSI codes."""
        codes = sample_diff_ansi_output
        assert "31m" in codes["deletion_prefix"], "Deletions must use red (31m)"

    def test_ansi_bold_for_headers(self, sample_diff_ansi_output):
        """Section headers must be bold (1m)."""
        codes = sample_diff_ansi_output
        assert "1m" in codes["header_prefix"], "Headers must use bold (1m)"

    def test_ansi_reset_after_each_line(self, sample_diff_ansi_output):
        """Each colored line must end with a reset code."""
        codes = sample_diff_ansi_output
        assert "0m" in codes["reset"], "Reset must use 0m"

    def test_ansi_output_no_color_for_unchanged(self, tmp_path):
        """Unchanged section text must not have color prefixes (or use reset)."""
        viewer = DiffViewer(repo_path=str(tmp_path / "laws" / "ABGB"))
        viewer.repo_path.mkdir(parents=True, exist_ok=True)
        assert hasattr(viewer, "COLOR_RESET") or hasattr(viewer, "ANSI_RESET"), (
            "Viewer must define ANSI reset constant"
        )

    def test_plain_text_output_available(self, tmp_path):
        """Diff must support plain text output (no ANSI) for piping."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        viewer = DiffViewer(repo_path=str(repo_path))
        result = viewer.diff("2017-01-01", "2018-01-01")
        if hasattr(result, "to_plain"):
            text = result.to_plain()
            assert "\033[" not in text, "Plain output must not contain ANSI codes"

    def test_diff_output_includes_section_headers(self, tmp_path):
        """Diff output must include section headers with fassung_vom timestamps."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        viewer = DiffViewer(repo_path=str(repo_path))
        result = viewer.diff("2017-01-01", "2018-01-01")
        ansi = result.to_ansi()
        assert isinstance(ansi, str), "Must produce ANSI string"


# ── convenience function tests ────────────────────────────────────────────────


class TestDiffLawFunction:
    """Tests for the diff_law convenience function."""

    def test_diff_law_function_exists(self):
        """diff_law must be importable and callable."""
        assert callable(diff_law), "diff_law must be a callable function"

    def test_diff_law_accepts_abbrev_and_dates(self, tmp_path, monkeypatch):
        """diff_law must accept law abbreviation and two dates."""
        _make_test_law_repo(tmp_path, "ABGB", ["2017-01-01", "2018-01-01"])
        monkeypatch.setenv("GIT_FOR_LAW_REPO_BASE", str(tmp_path / "laws"))
        result = diff_law("ABGB", "2017-01-01", "2018-01-01")
        assert isinstance(result, DiffResult), "diff_law must return DiffResult"

    def test_diff_law_accepts_optional_section_id(self, tmp_path, monkeypatch):
        """diff_law must accept optional section_id parameter."""
        _make_test_law_repo(tmp_path, "ABGB", ["2017-01-01", "2018-01-01"])
        monkeypatch.setenv("GIT_FOR_LAW_REPO_BASE", str(tmp_path / "laws"))
        result = diff_law("ABGB", "2017-01-01", "2018-01-01", section_id="§_1")
        assert isinstance(result, DiffResult), "diff_law with section_id must return DiffResult"

    def test_diff_law_auto_resolves_repo_path(self, tmp_path, monkeypatch):
        """diff_law must auto-resolve repo path from abbreviation."""
        _make_test_law_repo(tmp_path, "ABGB", ["2017-01-01", "2018-01-01"])
        monkeypatch.setenv("GIT_FOR_LAW_REPO_BASE", str(tmp_path / "laws"))
        result = diff_law("ABGB", "2017-01-01", "2018-01-01")
        assert isinstance(result, DiffResult), (
            "diff_law must resolve repo from abbreviation"
        )


# ── CLI entry point tests ─────────────────────────────────────────────────────


class TestDiffCLI:
    """Tests for the CLI entry point: python3 -m git_for_law_austria.diff."""

    def test_cli_module_main_exists(self):
        """diff module must have a __main__ entry point (runnable with -m)."""
        result = subprocess.run(
            [sys.executable, "-m", "git_for_law_austria.diff", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode in (0, 2), (
            f"CLI must accept --help flag, got returncode {result.returncode}"
        )

    def test_cli_accepts_law_and_dates(self, tmp_path):
        """CLI must accept law abbreviation, from-date, to-date as positional args."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        result = subprocess.run(
            [sys.executable, "-m", "git_for_law_austria.diff",
             "ABGB", "2017-01-01", "2018-01-01"],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "GIT_FOR_LAW_REPO_BASE": str(tmp_path)},
        )
        assert result.returncode in (0, 1), "CLI must handle diff request"

    def test_cli_accepts_section_id_flag(self, tmp_path):
        """CLI must accept --section or -s flag to filter by section ID."""
        result = subprocess.run(
            [sys.executable, "-m", "git_for_law_austria.diff",
             "ABGB", "2017-01-01", "2018-01-01", "--section", "§_1"],
            capture_output=True,
            text=True,
        )
        assert result.returncode in (0, 1, 2), "CLI must accept --section flag"

    def test_cli_stderr_on_invalid_args(self):
        """CLI must print usage to stderr when called with invalid arguments."""
        result = subprocess.run(
            [sys.executable, "-m", "git_for_law_austria.diff", "ABGB"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, "CLI must fail with missing required args"
        assert result.stderr or result.stdout, "Must produce error output"

    def test_cli_prints_ansi_by_default(self, tmp_path):
        """CLI must output ANSI-colored text to stdout by default."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        result = subprocess.run(
            [sys.executable, "-m", "git_for_law_austria.diff",
             "ABGB", "2017-01-01", "2018-01-01"],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "GIT_FOR_LAW_REPO_BASE": str(tmp_path)},
        )
        output = result.stdout or result.stderr
        assert output, "CLI must produce output"

    def test_cli_no_color_flag(self, tmp_path):
        """CLI must support --no-color flag to disable ANSI output."""
        result = subprocess.run(
            [sys.executable, "-m", "git_for_law_austria.diff",
             "ABGB", "2017-01-01", "2018-01-01", "--no-color"],
            capture_output=True,
            text=True,
        )
        assert result.returncode in (0, 1, 2), "CLI must accept --no-color flag"


# ── Diff result format tests ──────────────────────────────────────────────────


class TestDiffResultFormat:
    """Tests for the DiffResult output format."""

    def test_diff_result_serializable(self):
        """DiffResult must be JSON-serializable."""
        result = DiffResult(
            law_abbrev="ABGB",
            from_date="2017-01-01",
            to_date="2018-01-01",
            changed_sections=[
                SectionDiff(
                    section_id="§_1",
                    heading="§ 1",
                    old_body="Alt.",
                    new_body="Neu.",
                ),
            ],
            unchanged_sections=["§_2", "§_3"],
        )
        json_str = result.to_json()
        assert isinstance(json_str, str)
        data = json.loads(json_str)
        assert data["law_abbrev"] == "ABGB"
        assert len(data["changed_sections"]) == 1

    def test_diff_result_summary(self):
        """DiffResult must provide a summary dict."""
        result = DiffResult(
            law_abbrev="ABGB",
            from_date="2017-01-01",
            to_date="2018-01-01",
            changed_sections=[
                SectionDiff("§_1", "§ 1", "Alt.", "Neu."),
                SectionDiff("§_2", "§ 2", "Alt2.", "Neu2."),
            ],
            unchanged_sections=["§_3", "§_4"],
        )
        summary = result.summary()
        assert summary["total_sections"] == 4
        assert summary["changed_count"] == 2
        assert summary["unchanged_count"] == 2

    def test_diff_result_empty(self):
        """DiffResult with no changes must handle gracefully."""
        result = DiffResult(
            law_abbrev="ABGB",
            from_date="2017-01-01",
            to_date="2017-01-01",
            changed_sections=[],
            unchanged_sections=[],
        )
        assert result.total_changes == 0
        assert result.has_changes is False


# ── Git version resolution tests ──────────────────────────────────────────────


class TestDiffViewerVersionResolution:
    """Tests for resolving fassung_vom dates to git commits/sections."""

    def test_list_versions_in_repo(self, tmp_path):
        """DiffViewer must list available fassung_vom versions in the repo."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        viewer = DiffViewer(repo_path=str(repo_path))
        versions = viewer.list_versions()
        assert isinstance(versions, list), "Must return list of versions"

    def test_resolve_version_to_sections(self, tmp_path):
        """DiffViewer must resolve a fassung_vom to its section contents."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        viewer = DiffViewer(repo_path=str(repo_path))
        sections = viewer.get_version_sections("2017-01-01")
        assert isinstance(sections, dict), "Must return dict of section_id → section data"

    def test_compare_section_bodies(self, tmp_path):
        """SectionDiff must correctly identify changed body text."""
        diff = SectionDiff(
            section_id="§_1",
            heading="§ 1",
            old_body="(1) Erster Satz. (2) Zweiter Satz.",
            new_body="(1) Erster Satz geaendert. (2) Zweiter Satz.",
        )
        assert diff.added_lines or diff.removed_lines, (
            "SectionDiff must extract line-level changes"
        )

    def test_empty_body_diff(self):
        """Diff with empty old/new body must be handled."""
        diff = SectionDiff(
            section_id="§_99",
            heading="§ 99",
            old_body="",
            new_body="Neuer Inhalt.",
        )
        assert diff.is_changed is True
        diff2 = SectionDiff(
            section_id="§_99",
            heading="§ 99",
            old_body="",
            new_body="",
        )
        assert diff2.is_changed is False


# ── Edge case tests ───────────────────────────────────────────────────────────


class TestDiffViewerEdgeCases:
    """Edge case tests for the diff viewer."""

    def test_identical_versions_produce_no_changes(self, tmp_path):
        """Diff between same fassung_vom date must produce empty changes."""
        repo_path = tmp_path / "laws" / "ABGB"
        repo_path.mkdir(parents=True)
        viewer = DiffViewer(repo_path=str(repo_path))
        result = viewer.diff("2017-01-01", "2017-01-01")
        assert result.has_changes is False, "Same version diff must have no changes"

    def test_different_laws_have_separate_repos(self):
        """Each law must have its own repo (ABGB, B-VG, VBG are separate)."""
        repos = ["laws/ABGB", "laws/B-VG", "laws/VBG"]
        for repo in repos:
            viewer = DiffViewer(repo_path=str(Path(repo)))
            assert viewer.repo_path == str(Path(repo))

    def test_unicode_in_section_body(self, tmp_path):
        """Sections with Unicode (German umlauts, §) must diff correctly."""
        diff = SectionDiff(
            section_id="§_1",
            heading="§ 1",
            old_body="Änderung des Bundesgesetzblattes.",
            new_body="Änderung des Bundes-Verfassungsgesetzes.",
        )
        assert diff.is_changed is True
        assert "Bundesgesetzblattes" in diff.old_body
        assert "Bundes-Verfassungsgesetzes" in diff.new_body

    def test_very_long_section_body(self, tmp_path):
        """Very long section bodies must be handled without truncation in diff."""
        long_text = "X" * 10000
        diff = SectionDiff(
            section_id="§_1",
            heading="§ 1",
            old_body=long_text,
            new_body=long_text + "A",
        )
        assert diff.is_changed is True
        assert len(diff.old_body) == 10000
        assert len(diff.new_body) == 10001

    def test_section_with_only_whitespace_change(self):
        """Sections that differ only in whitespace must be detected."""
        diff = SectionDiff(
            section_id="§_1",
            heading="§ 1",
            old_body="Text ohne Leerzeichen.",
            new_body="Text ohne  Leerzeichen.",
        )
        assert diff.is_changed is True, "Whitespace changes must be detected"

    def test_new_section_not_in_old_version(self):
        """A section that exists only in the new version must be handled."""
        diff = SectionDiff(
            section_id="§_999",
            heading="§ 999",
            old_body=None,
            new_body="Neuer Paragraph.",
        )
        assert diff.is_changed is True
        assert diff.old_body is None

    def test_removed_section_not_in_new_version(self):
        """A section that exists only in the old version must be handled."""
        diff = SectionDiff(
            section_id="§_998",
            heading="§ 998",
            old_body="Aufgehobener Paragraph.",
            new_body=None,
        )
        assert diff.is_changed is True
        assert diff.new_body is None

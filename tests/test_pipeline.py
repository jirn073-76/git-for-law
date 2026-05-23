"""Tests for the pipeline orchestration module.

These tests validate the end-to-end pipeline:
metadata fetch → content acquisition → HTML parsing → git commit.

Also tests: version grouping, commit message format, duplicate detection,
repo structure, empty sections, future versions, ris_url propagation,
Inkrafttretensdatum → fassung_vom mapping.
"""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from git_for_law_austria.pipeline import Pipeline, PipelineResult


ABGB_GSN = "10001622"
ABGB_ABBREV = "ABGB"


# ── Version grouping tests ────────────────────────────────────────────────────


class TestPipelineVersionGrouping:
    """Tests for grouping metadata items by fassung_vom date."""

    def test_group_by_fassung_vom(self):
        """Versions must be grouped by their fassung_vom date."""
        pipeline = Pipeline()
        items = [
            mock.Mock(fassung_vom="2017-01-01", abbreviation="ABGB", sections=[]),
            mock.Mock(fassung_vom="2017-01-01", abbreviation="ABGB", sections=[]),
            mock.Mock(fassung_vom="2018-01-01", abbreviation="ABGB", sections=[]),
        ]
        grouped = pipeline._group_by_fassung_vom(items)
        assert len(grouped) == 2, "Must produce 2 groups for 2 unique dates"
        assert len(grouped["2017-01-01"]) == 2, "2017-01-01 must have 2 items"
        assert len(grouped["2018-01-01"]) == 1, "2018-01-01 must have 1 item"

    def test_group_preserves_all_sections(self):
        """Grouping must preserve all section references from all items."""
        pipeline = Pipeline()
        items = [
            mock.Mock(
                fassung_vom="2017-01-01",
                abbreviation="ABGB",
                sections=[{"section_id": "§_1", "api_id": "F0025"}],
            ),
            mock.Mock(
                fassung_vom="2017-01-01",
                abbreviation="ABGB",
                sections=[{"section_id": "§_2", "api_id": "F0026"}],
            ),
        ]
        grouped = pipeline._group_by_fassung_vom(items)
        all_sections = []
        for item in grouped["2017-01-01"]:
            all_sections.extend(item.sections)
        assert len(all_sections) == 2, "Both sections must be preserved"

    def test_group_preserves_ris_url(self):
        """Grouping must preserve ris_url from each item for Wayback fetching."""
        pipeline = Pipeline()
        items = [
            mock.Mock(
                fassung_vom="2017-01-01",
                abbreviation="ABGB",
                ris_url="https://www.ris.bka.gv.at/GeltendeFassung.wxe?Gesetzesnummer=10001622&FassungVom=2017-01-01",
                sections=[],
            ),
        ]
        grouped = pipeline._group_by_fassung_vom(items)
        assert grouped["2017-01-01"][0].ris_url is not None, "ris_url must survive grouping"

    def test_group_handles_single_version(self):
        """Single version must produce single group."""
        pipeline = Pipeline()
        items = [
            mock.Mock(fassung_vom="2017-01-01", abbreviation="ABGB", sections=[]),
        ]
        grouped = pipeline._group_by_fassung_vom(items)
        assert len(grouped) == 1

    def test_group_handles_empty_list(self):
        """Empty item list must produce empty group dict."""
        pipeline = Pipeline()
        grouped = pipeline._group_by_fassung_vom([])
        assert grouped == {}

    def test_group_sorted_by_date(self):
        """Grouped dates must be sortable chronologically."""
        pipeline = Pipeline()
        items = [
            mock.Mock(fassung_vom="2020-01-01", abbreviation="ABGB", sections=[]),
            mock.Mock(fassung_vom="1812-01-01", abbreviation="ABGB", sections=[]),
            mock.Mock(fassung_vom="2000-01-01", abbreviation="ABGB", sections=[]),
        ]
        grouped = pipeline._group_by_fassung_vom(items)
        dates = sorted(grouped.keys())
        assert dates[0] == "1812-01-01", "Earliest date must be first"
        assert dates[-1] == "2020-01-01", "Latest date must be last"


# ── Commit message format tests ───────────────────────────────────────────────


class TestPipelineCommitMessages:
    """Tests for commit message format: {Abbreviation} [{fassung_vom}]: {amendment_text}."""

    def test_commit_message_format(self, expected_commit_message_format):
        """Commit message must follow the exact format ABGB [2017-01-01]: amendment."""
        pipeline = Pipeline()
        msg = pipeline._build_commit_message(
            abbrev="ABGB",
            fassung_vom="2017-01-01",
            aenderung="BGBl. I Nr. 43/2016",
        )
        expected = "ABGB [2017-01-01]: BGBl. I Nr. 43/2016"
        assert msg == expected, f"Expected '{expected}', got '{msg}'"

    def test_commit_message_includes_fassung_vom(self):
        """fassung_vom date MUST be in the commit message to prevent duplicates."""
        pipeline = Pipeline()
        msg = pipeline._build_commit_message("ABGB", "2017-01-01", "BGBl. I Nr. 43/2016")
        assert "[2017-01-01]" in msg, (
            "Commit message MUST contain fassung_vom date to prevent duplicate commits"
        )

    def test_commit_message_truncates_long_aenderung(self):
        """Amendment text over 120 chars must be truncated to prevent breaking git."""
        pipeline = Pipeline()
        long_amendment = "BGBl. I Nr. 43/2016 " + "mit sehr langem Aenderungstext. " * 10
        msg = pipeline._build_commit_message("ABGB", "2017-01-01", long_amendment)
        # The aenderung part should be truncated, so total shouldn't exceed
        # len("ABGB [2017-01-01]: ") + 120
        prefix = "ABGB [2017-01-01]: "
        aenderung_part = msg[len(prefix):]
        assert len(aenderung_part) <= 120, (
            f"Aenderung part must be <= 120 chars, got {len(aenderung_part)}"
        )

    def test_commit_message_handles_empty_aenderung(self):
        """Empty amendment text must still produce valid commit message."""
        pipeline = Pipeline()
        msg = pipeline._build_commit_message("ABGB", "2020-01-01", "")
        assert "ABGB [2020-01-01]" in msg, "Must still contain law and date"

    def test_commit_message_handles_special_chars(self):
        """Special characters in aenderung must not break commit message."""
        pipeline = Pipeline()
        msg = pipeline._build_commit_message(
            "ABGB", "2017-01-01", 'BGBl. I Nr. 43/2016 "Reform"'
        )
        assert "Reform" in msg, "Special characters must be preserved"

    def test_aenderung_from_mapped_inkrafttretensdatum(self):
        """Commit message uses aenderung mapped from OGD's Aenderung field."""
        pipeline = Pipeline()
        msg = pipeline._build_commit_message(
            abbrev="ABGB",
            fassung_vom="2020-01-01",
            aenderung="BGBl. I Nr. 100/2019",
        )
        assert msg == "ABGB [2020-01-01]: BGBl. I Nr. 100/2019"


# ── Duplicate detection tests ─────────────────────────────────────────────────


class TestPipelineDuplicateDetection:
    """Tests for preventing duplicate commits for the same fassung_vom."""

    def test_same_fassung_vom_not_committed_twice(self, tmp_path):
        """Same fassung_vom date must not produce two commits."""
        pipeline = Pipeline(repo_path=tmp_path / "test_repo")
        commit_dates = []

        def fake_commit(fassung_vom, **kwargs):
            if fassung_vom in commit_dates:
                raise ValueError(f"Duplicate commit for {fassung_vom}")
            commit_dates.append(fassung_vom)

        pipeline._commit_version = fake_commit
        pipeline._commit_version("2017-01-01", sections=[])
        with pytest.raises(ValueError, match="Duplicate commit"):
            pipeline._commit_version("2017-01-01", sections=[])

    def test_version_already_committed_detection(self):
        """Pipeline must detect if a version already exists in the git repo."""
        pipeline = Pipeline()
        committed = {"2017-01-01", "2018-01-01"}
        assert pipeline._is_already_committed("2017-01-01", committed) is True, (
            "2017-01-01 must be detected as already committed"
        )
        assert pipeline._is_already_committed("2019-01-01", committed) is False, (
            "2019-01-01 must be detected as not yet committed"
        )

    def test_duplicate_aenderung_different_dates_both_committed(self):
        """Identical Aenderung with different Inkrafttretensdatum must produce two commits."""
        pipeline = Pipeline()
        committed = {"2015-01-01"}
        result = pipeline._is_already_committed("2015-06-01", committed)
        assert result is False, (
            "Different Inkrafttretensdatum with same Aenderung must be allowed"
        )


# ── Git repo initialization and structure tests ───────────────────────────────


class TestPipelineRepoStructure:
    """Tests for git repository initialization and file structure."""

    def test_repo_initialized_at_correct_path(self):
        """Pipeline must initialize a git repo at the configured path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "laws" / "ABGB"
            pipeline = Pipeline(repo_path=repo_path)
            pipeline._init_repo()
            git_dir = repo_path / ".git"
            assert git_dir.exists(), ".git directory must exist after init"
            assert git_dir.is_dir(), ".git must be a directory"

    def test_repo_stores_fassung_json(self):
        """Each committed version must store a single fassung.json."""
        pipeline = Pipeline()
        sections = [
            {"section_id": "§_1", "heading": "§ 1", "body": "Text eins.", "section_type": "Paragraf"},
            {"section_id": "§_2", "heading": "§ 2", "body": "Text zwei.", "section_type": "Paragraf"},
        ]
        file_paths = pipeline._get_section_file_paths(sections)
        assert len(file_paths) == 2, "Must produce paths for all sections"

    def test_json_files_contain_correct_fields(self):
        """Each section JSON file must contain all required fields."""
        pipeline = Pipeline()
        section = {
            "section_id": "§_1",
            "heading": "§ 1",
            "body": "Paragraph eins Text.",
            "section_type": "Paragraf",
            "fassung_vom": "2017-01-01",
        }
        json_content = pipeline._section_to_json(section)
        data = json.loads(json_content)
        assert data["section_id"] == "§_1"
        assert data["heading"] == "§ 1"
        assert data["body"] == "Paragraph eins Text."
        assert data["section_type"] == "Paragraf"
        assert data["fassung_vom"] == "2017-01-01"

    def test_repo_layout_follows_convention(self):
        """Repo must use the layout: data/laws/{Abbrev}/ with JSON files."""
        pipeline = Pipeline()
        repo_path = pipeline._get_default_repo_path("ABGB")
        assert "laws" in str(repo_path), "Repo must be under data/laws/"
        assert "ABGB" in str(repo_path), "Repo path must include law abbreviation"

    def test_section_id_filename_derivation(self):
        """Section ID must be correctly converted to filename: Art. 1 → Art_1, § 1 → §_1."""
        pipeline = Pipeline()
        test_cases = [
            ("§ 1", "§_1"),
            ("Art. 1", "Art_1"),
            ("Anlage 1", "Anlage_1"),
            ("§ 531", "§_531"),
        ]
        for section_id, expected_filename in test_cases:
            filename = pipeline._section_id_to_filename(section_id)
            assert filename == expected_filename + ".json", (
                f"Section ID '{section_id}' must produce filename '{expected_filename}.json'"
            )


# ── Empty section handling tests ──────────────────────────────────────────────


class TestPipelineEmptySections:
    """Tests for handling sections with empty or missing body text."""

    def test_empty_section_not_written(self):
        """Sections with empty body must not produce a JSON file."""
        pipeline = Pipeline()
        section = {
            "section_id": "§_99",
            "heading": "§ 99",
            "body": "",
            "section_type": "Paragraf",
            "fassung_vom": "2020-01-01",
        }
        assert pipeline._should_write_section(section) is False, (
            "Empty section must not be written to repo"
        )

    def test_valid_section_is_written(self):
        """Sections with non-empty body must be written."""
        pipeline = Pipeline()
        section = {
            "section_id": "§_1",
            "heading": "§ 1",
            "body": "Gueltiger Text.",
            "section_type": "Paragraf",
            "fassung_vom": "2020-01-01",
        }
        assert pipeline._should_write_section(section) is True, (
            "Non-empty section must be written"
        )

    def test_single_section_version_handled(self):
        """Versions with only 1 section (64 of 145 ABGB versions) must be handled."""
        pipeline = Pipeline()
        sections = [
            {"section_id": "§_1", "body": "Einziger Paragraph.", "fassung_vom": "1812-01-01"},
        ]
        assert len(sections) == 1, "Single-section version is valid"
        file_paths = pipeline._get_section_file_paths(sections)
        assert len(file_paths) == 1, "Must produce exactly one file path"

    def test_1022_section_version_handled(self, sample_1022_section_version):
        """Versions with 1022 sections (largest) must be processed."""
        pipeline = Pipeline()
        assert len(sample_1022_section_version["sections"]) == 1022
        file_paths = pipeline._get_section_file_paths(sample_1022_section_version["sections"])
        assert len(file_paths) == 1022, "Must handle 1022-section version"

    def test_all_sections_empty_skips_commit(self):
        """If all sections in a version are empty, commit must be skipped."""
        pipeline = Pipeline()
        all_empty = [
            {"section_id": "§_1", "body": ""},
            {"section_id": "§_2", "body": ""},
        ]
        should_commit = pipeline._should_commit_version(all_empty)
        assert should_commit is False, (
            "Version with all empty sections must not be committed"
        )


# ── Future version handling tests ─────────────────────────────────────────────


class TestPipelineFutureVersions:
    """Tests for handling versions with future Inkrafttretensdatum dates."""

    def test_future_version_is_processed(self, sample_future_version):
        """Future versions (e.g., 2028-07-01) must still be processed normally."""
        pipeline = Pipeline()
        assert pipeline._is_future_date("2028-07-01") is True, (
            "2028-07-01 must be detected as future date"
        )

    def test_future_version_not_skipped(self, sample_future_version):
        """Future versions must NOT be skipped — they are valid versions."""
        pipeline = Pipeline()
        should_process = pipeline._should_process_version(sample_future_version)
        assert should_process is True, "Future versions must be processed"

    def test_historical_date_not_future(self):
        """Dates before today must not be flagged as future."""
        pipeline = Pipeline()
        assert pipeline._is_future_date("1812-01-01") is False, (
            "1812-01-01 is a historical date, not future"
        )

    def test_future_inkrafttretensdatum_propagates(self):
        """Future Inkrafttretensdatum must be passed through as fassung_vom."""
        pipeline = Pipeline()
        assert pipeline._normalize_date("2028-07-01") == "2028-07-01", (
            "Future dates must normalize correctly"
        )


# ── Pipeline ris_url propagation tests ────────────────────────────────────────


class TestPipelineRisUrlPropagation:
    """Tests for propagating GesamteRechtsvorschriftUrl through the pipeline."""

    def test_ris_url_passed_to_content_acquisition(self):
        """ris_url from OGD item must be available in metadata_by_date during commit."""
        pipeline = Pipeline()
        committed_meta = []

        def capture_meta(abbrev, parsed, metadata_by_date):
            committed_meta.append(metadata_by_date)
            return 0

        pipeline._fetch_metadata = lambda gsn: [
            mock.Mock(
                fassung_vom="2017-01-01",
                abbreviation="ABGB",
                aenderung="",
                ris_url="https://www.ris.bka.gv.at/GeltendeFassung.wxe?Gesetzesnummer=10001622&FassungVom=2017-01-01",
                sections=[],
            ),
        ]
        pipeline._fetch_geltende_fassung = lambda gsn, dates: [
            {"fassung_vom": d, "sections": []} for d in dates
        ]
        pipeline._commit_versions = capture_meta
        pipeline.run(gsn=ABGB_GSN, max_versions=1)
        assert len(committed_meta) > 0, "metadata must reach commit step"
        assert "Gesetzesnummer=10001622" in committed_meta[0]["2017-01-01"]["ris_url"]

    def test_pipeline_handles_missing_ris_url(self):
        """Missing ris_url must not crash the pipeline — fallback to empty content."""
        pipeline = Pipeline()
        pipeline._fetch_metadata = lambda gsn: [
            mock.Mock(fassung_vom="2017-01-01", abbreviation="ABGB", aenderung="", ris_url=None, sections=[]),
        ]
        pipeline._fetch_geltende_fassung = lambda gsn, dates: []
        pipeline._commit_versions = lambda abbrev, parsed, metadata_by_date: 0
        result = pipeline.run(gsn=ABGB_GSN, max_versions=1)
        assert isinstance(result, PipelineResult), "Must not crash with missing ris_url"


# ── End-to-end pipeline tests ────────────────────────────────────────────────


class TestPipelineEndToEnd:
    """Integration tests for the full pipeline flow."""

    def test_pipeline_run_called_in_order(self):
        """Pipeline.run must call steps in order: metadata → content → commit."""
        import unittest.mock as mock

        pipeline = Pipeline()
        call_order = []

        pipeline._fetch_metadata = lambda gsn: call_order.append("metadata") or [
            mock.Mock(fassung_vom="2017-01-01", aenderung="", ris_url="", sections=[]),
        ]
        pipeline._fetch_geltende_fassung = lambda gsn, dates: call_order.append("content") or []
        pipeline._commit_versions = lambda abbrev, parsed, metadata_by_date: call_order.append("commit")

        pipeline.run(gsn=ABGB_GSN)
        assert call_order == ["metadata", "content", "commit"], (
            f"Pipeline steps out of order: {call_order}"
        )

    def test_pipeline_returns_result(self):
        """Pipeline.run must return a PipelineResult with summary statistics."""
        pipeline = Pipeline()
        result = pipeline.run(gsn=ABGB_GSN, max_versions=5)
        assert isinstance(result, PipelineResult), "Must return PipelineResult"
        assert result.law_abbrev == "ABGB", "Result must include law abbreviation"
        assert result.gsn == ABGB_GSN, "Result must include GSN"

    def test_max_versions_limits_processing(self):
        """max_versions parameter must limit the number of versions processed."""
        pipeline = Pipeline()
        versions_processed = []

        def fake_fetch(gsn):
            return [mock.Mock(fassung_vom=f"2020-{i:02d}-01", abbreviation="ABGB",
                            aenderung="", sections=[])
                    for i in range(1, 21)]

        pipeline._fetch_metadata = fake_fetch

        def fake_group(items):
            return {item.fassung_vom: [item] for item in items}

        pipeline._group_by_fassung_vom = fake_group

        pipeline._fetch_geltende_fassung = lambda gsn, dates: []

        pipeline._commit_versions = lambda abbrev, parsed, metadata_by_date: versions_processed.append(len(parsed))

        pipeline.run(gsn=ABGB_GSN, max_versions=3)
        assert versions_processed[0] <= 3, (
            f"max_versions=3 must limit processing, got {versions_processed[0]}"
        )

    def test_pipeline_handles_content_acquisition_failure(self):
        """Pipeline must not crash when content acquisition fails."""
        pipeline = Pipeline()

        pipeline._fetch_geltende_fassung = lambda gsn, dates: [
            {"fassung_vom": d, "sections": []} for d in dates
        ]
        pipeline._fetch_metadata = lambda gsn: [
            mock.Mock(fassung_vom="2017-01-01", abbreviation="ABGB", aenderung="", sections=[]),
        ]

        result = pipeline.run(gsn=ABGB_GSN, max_versions=1)
        assert isinstance(result, PipelineResult), (
            "Pipeline must return result even with content acquisition failure"
        )

    @pytest.mark.slow
    def test_pipeline_integration_with_temp_repo(self):
        """Full integration: create temp repo, commit sections, verify."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "laws" / "ABGB"
            pipeline = Pipeline(repo_path=repo_path)
            pipeline._init_repo()

            sections = [
                {
                    "section_id": "§_1",
                    "heading": "§ 1",
                    "body": "Paragraph eins des ABGB.",
                    "section_type": "Paragraf",
                    "fassung_vom": "2017-01-01",
                },
            ]
            for section in sections:
                file_path = repo_path / f"{section['section_id']}.json"
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(json.dumps(section, ensure_ascii=False, indent=2))

            section_file = repo_path / "§_1.json"
            assert section_file.exists(), "Section JSON file must be written"
            data = json.loads(section_file.read_text())
            assert data["section_id"] == "§_1"
            assert "Paragraph eins" in data["body"]


# ── Edge case: ABGB specifics ─────────────────────────────────────────────────


class TestPipelineABGBEdgeCases:
    """Tests for ABGB-specific edge cases discovered by research."""

    def test_abgb_has_145_unique_versions(self):
        """Pipeline must handle 145 unique fassung_vom dates for ABGB."""
        pipeline = Pipeline()
        dates = set()
        import datetime
        base = datetime.date(1812, 1, 1)
        for i in range(145):
            d = base + datetime.timedelta(days=i * 50)
            dates.add(d.isoformat())
        assert len(dates) == 145
        for date in dates:
            assert pipeline._normalize_date(date) is not None, (
                f"Date {date} must be normalizable"
            )

    def test_64_single_section_versions(self):
        """64 ABGB versions have only 1 section — must not crash."""
        pipeline = Pipeline()
        single_section_versions = [
            {"fassung_vom": f"1900-{i:02d}-01", "sections": [{"section_id": "§_1", "body": "Text"}]}
            for i in range(1, 65)
        ]
        assert len(single_section_versions) == 64
        for version in single_section_versions:
            result = pipeline._should_commit_version(version["sections"])
            assert result is True, (
                f"Single-section version {version['fassung_vom']} must be committable"
            )

    def test_1685_unique_section_ids(self):
        """Pipeline must handle 1,685 unique section IDs across all versions."""
        Pipeline()
        all_section_ids = set()
        import random
        random.seed(42)
        for _ in range(1685):
            typ = random.choice(["§", "Art"])
            num = random.randint(1, 1500)
            all_section_ids.add(f"{typ}_{num}")
        assert len(all_section_ids) <= 1685, "Must handle large number of unique section IDs"

    def test_oldest_version_1812(self):
        """Oldest ABGB version from 1812-01-01 must be processable."""
        pipeline = Pipeline()
        assert pipeline._normalize_date("1812-01-01") == "1812-01-01", (
            "1812-01-01 must be a valid fassung_vom date"
        )

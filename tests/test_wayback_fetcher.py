"""Tests for the Wayback Machine / content fetcher module.

These tests validate:
- Direct Wayback URL construction from GesamteRechtsvorschriftUrl (NOT CDX-based)
- CDX API is known to NOT index RIS query-parameter URLs
- curl -L -A 'GitForLaw/1.0' behavior (follow redirects, set User-Agent)
- Graceful handling of missing Wayback snapshots
- Construction of snapshot URLs from fassung_vom timestamps
"""

import pytest

from git_for_law_austria.wayback_fetcher import WaybackFetcher, CDXResult, ContentSource


CDX_API_URL = "https://web.archive.org/cdx/search/cdx"


# ── Wayback URL construction tests (from GesamteRechtsvorschriftUrl) ───────────


class TestWaybackFetcherURLConstruction:
    """Tests for constructing Wayback Machine URLs from OGD metadata."""

    def test_wayback_url_from_ris_url_and_date(self, sample_ris_url_from_ogd):
        """Wayback URL must be constructed from RIS URL + fassung_vom timestamp."""
        fetcher = WaybackFetcher()
        url = fetcher._build_wayback_url_from_ris(
            ris_url=sample_ris_url_from_ogd,
            fassung_vom="2017-01-01",
        )
        assert "web.archive.org" in url, "Must point to web.archive.org"
        assert "20170101" in url, "Must embed date as YYYYMMDD in URL"
        assert "GeltendeFassung" in url, "Must include the original RIS path"

    def test_wayback_url_uses_id_suffix(self, sample_ris_url_from_ogd):
        """Wayback snapshot URLs use the id_ suffix for identity-based playback."""
        fetcher = WaybackFetcher()
        url = fetcher._build_wayback_url_from_ris(
            ris_url=sample_ris_url_from_ogd,
            fassung_vom="2017-01-01",
        )
        assert "id_" in url, "Wayback URL must use id_ suffix after timestamp"

    def test_wayback_url_includes_full_ris_url(self, sample_ris_url_from_ogd):
        """The full RIS GesamteRechtsvorschriftUrl must be in the Wayback URL."""
        fetcher = WaybackFetcher()
        url = fetcher._build_wayback_url_from_ris(
            ris_url=sample_ris_url_from_ogd,
            fassung_vom="2017-01-01",
        )
        assert "Abfrage=Bundesnormen" in url, "Must preserve RIS query parameters"
        assert "Gesetzesnummer=10001622" in url, "Must preserve Gesetzesnummer"
        assert "FassungVom=2017-01-01" in url, "Must preserve FassungVom"

    def test_fassung_vom_converted_to_compact_timestamp(self):
        """fassung_vom YYYY-MM-DD must be converted to YYYYMMDDhhmmss for Wayback."""
        fetcher = WaybackFetcher()
        fetcher._fassung_vom_to_wayback_timestamp("2017-01-01")
        ts = fetcher._fassung_vom_to_wayback_timestamp("2017-01-01")
        assert ts == "20170101120000", (
            f"fassung_vom must convert to YYYYMMDD120000, got {ts}"
        )

    def test_direct_url_construction_does_not_use_cdx(self, sample_ris_url_from_ogd):
        """Primary fetch path must NOT use CDX — it constructs Wayback URLs directly."""
        fetcher = WaybackFetcher()
        url = fetcher._build_wayback_url_from_ris(
            ris_url=sample_ris_url_from_ogd,
            fassung_vom="2017-01-01",
        )
        assert "cdx" not in url.lower(), (
            "Direct Wayback URL must not reference CDX API"
        )


# ── curl -L -A behavior tests ─────────────────────────────────────────────────


class TestWaybackFetcherCurlBehavior:
    """Tests for curl-based fetching with -L (follow redirects) and -A (User-Agent)."""

    def test_user_agent_set_to_git_for_law(self):
        """WaybackFetcher must set User-Agent to 'GitForLaw/1.0'."""
        fetcher = WaybackFetcher()
        assert fetcher.user_agent == "GitForLaw/1.0", (
            "User-Agent must be 'GitForLaw/1.0'"
        )

    def test_follow_redirects_enabled(self):
        """WaybackFetcher must follow HTTP redirects (equivalent to curl -L)."""
        fetcher = WaybackFetcher()
        assert fetcher.follow_redirects is True, (
            "Must follow redirects (curl -L equivalent)"
        )

    def test_fetch_uses_user_agent_header(self):
        """All fetch requests must include the GitForLaw User-Agent header."""
        fetcher = WaybackFetcher()
        headers = fetcher._build_fetch_headers()
        assert headers["User-Agent"] == "GitForLaw/1.0", (
            "Fetch headers must include GitForLaw/1.0 User-Agent"
        )

    def test_fetch_without_user_agent_returns_empty(self):
        """Without User-Agent header, Wayback returns empty 302 (real behavior)."""
        fetcher = WaybackFetcher()
        assert fetcher._warn_if_no_user_agent({"User-Agent": ""}) is True or (
            fetcher._warn_if_no_user_agent({}) is True
        ), "Must warn when User-Agent is missing/empty"

    def test_redirect_response_handled(self, sample_wayback_redirect_response):
        """302 redirect from Wayback must be followed, not treated as final response."""
        fetcher = WaybackFetcher()
        is_redirect = fetcher._is_redirect(sample_wayback_redirect_response["status_code"])
        assert is_redirect is True, (
            f"Status {sample_wayback_redirect_response['status_code']} must be detected as redirect"
        )

    def test_direct_ris_access_never_called(self):
        """WaybackFetcher must NEVER access www.ris.bka.gv.at directly."""
        fetcher = WaybackFetcher()
        assert fetcher.ris_direct_access_enabled is False, (
            "Direct RIS access must be disabled — all traffic goes through Wayback"
        )


# ── CDX API limitations (CDX does NOT index RIS query-parameter URLs) ─────────


class TestWaybackFetcherCDXLimitations:
    """Tests for handling CDX API limitations with RIS URLs."""

    def test_cdx_does_not_index_ris_query_urls(self):
        """CDX API is known to NOT index RIS query-parameter URLs — must be documented."""
        fetcher = WaybackFetcher()
        assert fetcher.cdx_supports_ris_urls is False, (
            "CDX does not index RIS GeltendeFassung.wxe?Abfrage=... URLs"
        )

    def test_cdx_fallback_disabled_for_ris(self):
        """When CDX is disabled for RIS, the fetcher must use direct Wayback construction."""
        fetcher = WaybackFetcher()
        ris_url = (
            "https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
            "Abfrage=Bundesnormen&Gesetzesnummer=10001622"
        )
        # For RIS query URLs, direct Wayback URL must be used, not CDX
        strategy = fetcher._choose_fetch_strategy(ris_url)
        assert strategy == "direct_wayback", (
            f"For RIS query URLs, strategy must be 'direct_wayback', got {strategy}"
        )

    def test_cdx_still_usable_for_non_ris_urls(self):
        """CDX can still be used for non-RIS URLs (general Wayback lookup)."""
        fetcher = WaybackFetcher()
        url = "https://example.com/some-page"
        strategy = fetcher._choose_fetch_strategy(url)
        assert strategy == "cdx", (
            f"For non-RIS URLs, strategy must be 'cdx', got {strategy}"
        )

    def test_empty_cdx_no_longer_blocks_fetch(self):
        """Since CDX is not the primary path, empty CDX must not cause fetch failure."""
        fetcher = WaybackFetcher()
        result = fetcher.fetch_content(
            ris_url=(
                "https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
                "Abfrage=Bundesnormen&Gesetzesnummer=10001622&FassungVom=2017-01-01"
            ),
            fassung_vom="2017-01-01",
        )
        # Must not raise even though CDX would fail for this URL
        assert isinstance(result, dict), "Must return result dict"
        assert "source" in result


# ── CDX response parsing tests (for non-RIS fallback) ─────────────────────────


class TestWaybackFetcherCDXResponseParsing:
    """Tests for parsing CDX API JSON responses (used for non-RIS URLs)."""

    def test_parse_cdx_response_extracts_snapshots(self, sample_cdx_response):
        """CDX response must be parsed into CDXResult objects."""
        fetcher = WaybackFetcher()
        results = fetcher._parse_cdx_response(sample_cdx_response)
        assert len(results) == 1, "Must parse one snapshot from sample response"
        assert isinstance(results[0], CDXResult), "Each result must be a CDXResult"

    def test_parse_cdx_stores_timestamp(self, sample_cdx_response):
        """CDXResult must store the snapshot timestamp."""
        fetcher = WaybackFetcher()
        results = fetcher._parse_cdx_response(sample_cdx_response)
        assert results[0].timestamp == "20170101120000", (
            "Must store original CDX timestamp"
        )

    def test_parse_cdx_filters_non_200(self):
        """CDX results with non-200 status codes must be filtered out."""
        fetcher = WaybackFetcher()
        response = [
            ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
            ["a", "20170101", "https://ris.bka.gv.at/GF/ABGB", "text/html", "404", "X", "0"],
            ["b", "20180101", "https://ris.bka.gv.at/GF/ABGB", "text/html", "200", "Y", "100"],
            ["c", "20190101", "https://ris.bka.gv.at/GF/ABGB", "text/html", "302", "Z", "0"],
        ]
        results = fetcher._parse_cdx_response(response)
        assert len(results) == 1, "Only 200-status snapshots must be kept"
        assert results[0].status_code == "200"


# ── Empty results and fallback tests ──────────────────────────────────────────


class TestWaybackFetcherEmptyResults:
    """Tests for handling empty Wayback results."""

    def test_empty_wayback_html_handled(self, sample_wayback_empty_response):
        """Empty Wayback response must return empty content, not crash."""
        fetcher = WaybackFetcher()
        content = fetcher._process_wayback_content(sample_wayback_empty_response)
        assert content == "" or content is None, (
            "Empty Wayback body must yield empty content"
        )

    def test_no_snapshot_for_date_handled(self):
        """When no snapshot exists for a fassung_vom date, fetcher must report clearly."""
        fetcher = WaybackFetcher()
        result = fetcher.fetch_content(
            ris_url=(
                "https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
                "Abfrage=Bundesnormen&Gesetzesnummer=10001622&FassungVom=1812-01-01"
            ),
            fassung_vom="1812-01-01",
        )
        assert result["content_available"] is False, (
            "Pre-1990s dates likely have no Wayback snapshots"
        )

    def test_empty_cdx_returns_empty_list(self, sample_cdx_empty_response):
        """Empty CDX response (header only) must return empty list, not crash."""
        fetcher = WaybackFetcher()
        results = fetcher._parse_cdx_response(sample_cdx_empty_response)
        assert results == [], "Empty CDX must produce empty list"

    def test_fetch_law_returns_empty_when_no_content(self):
        """fetch_law returns empty content dict for unarchived versions."""
        fetcher = WaybackFetcher()
        result = fetcher.fetch_law(
            law_abbrev="ABGB",
            fassung_vom="1812-01-01",
            ris_url="https://www.ris.bka.gv.at/GeltendeFassung.wxe?...",
        )
        assert result["content_available"] is False, (
            "content_available must be False for unarchived laws"
        )


# ── Content source enum tests ─────────────────────────────────────────────────


class TestWaybackFetcherContentSource:
    """Tests for ContentSource enum and strategy selection."""

    def test_content_source_enum(self):
        """ContentSource enum must define known source types."""
        assert hasattr(ContentSource, "WAYBACK"), "Must have WAYBACK source"
        assert hasattr(ContentSource, "OGD_METADATA_ONLY"), "Must have OGD_METADATA_ONLY source"
        assert hasattr(ContentSource, "MANUAL"), "Must have MANUAL source"

    def test_direct_wayback_is_default_strategy(self):
        """For RIS URLs, direct Wayback URL construction is the default strategy."""
        fetcher = WaybackFetcher()
        assert fetcher.default_strategy == "direct_wayback", (
            "Default strategy must be direct_wayback (not CDX-based)"
        )

    def test_content_source_determined_by_strategy(self):
        """ContentSource must reflect which strategy provided the content."""
        fetcher = WaybackFetcher()
        source = fetcher._determine_content_source(
            strategy="direct_wayback", content="<html>...valid...</html>"
        )
        assert source == ContentSource.WAYBACK, "Direct Wayback content must report WAYBACK source"

    def test_metadata_only_fallback(self):
        """When all fetch strategies fail, OGD_METADATA_ONLY must be the fallback."""
        fetcher = WaybackFetcher()
        source = fetcher._determine_content_source(strategy="direct_wayback", content="")
        assert source == ContentSource.OGD_METADATA_ONLY, (
            "Empty Wayback content must fallback to OGD_METADATA_ONLY"
        )


# ── Malformed response handling ───────────────────────────────────────────────


class TestWaybackFetcherMalformedResponses:
    """Tests for handling malformed responses."""

    def test_malformed_cdx_raises_gracefully(self, sample_cdx_malformed_response):
        """Malformed CDX response (not JSON) must raise a clear error."""
        fetcher = WaybackFetcher()
        with pytest.raises(Exception) as exc_info:
            fetcher._parse_cdx_response_text(sample_cdx_malformed_response)
        assert "cdx" in str(exc_info.value).lower() or "parse" in str(exc_info.value).lower(), (
            "Error must indicate CDX parsing failure"
        )

    def test_non_list_cdx_response_handled(self):
        """CDX response that is valid JSON but not a list must be handled."""
        fetcher = WaybackFetcher()
        with pytest.raises(Exception):
            fetcher._parse_cdx_response({"error": "not a list"})

    def test_network_timeout_handled(self):
        """Fetch timeout must be configurable."""
        fetcher = WaybackFetcher(timeout=1)
        assert fetcher.timeout == 1, "Timeout must be configurable"


# ── Wayback snapshot URL construction edge cases ──────────────────────────────


class TestWaybackFetcherSnapshotEdgeCases:
    """Tests for edge cases in snapshot URL construction."""

    def test_special_characters_in_ris_url(self):
        """RIS URLs with special characters must be handled in Wayback URL construction."""
        fetcher = WaybackFetcher()
        url = fetcher._build_wayback_url_from_ris(
            ris_url="https://www.ris.bka.gv.at/GeltendeFassung.wxe?Abfrage=Bundesnormen&Gesetzesnummer=10001622&FassungVom=2017-01-01",
            fassung_vom="2017-01-01",
        )
        assert "?" in url or "%3F" in url, "URL with query params must be encoded correctly"

    def test_fassung_vom_edge_dates(self):
        """Edge-case dates (very old, leap years) must produce valid timestamps."""
        fetcher = WaybackFetcher()
        assert fetcher._fassung_vom_to_wayback_timestamp("1812-01-01") is not None
        assert fetcher._fassung_vom_to_wayback_timestamp("2020-02-29") is not None

    def test_multiple_wayback_urls_per_version(self):
        """Multiple Wayback URLs may need to be tried per version (different timestamps)."""
        fetcher = WaybackFetcher()
        urls = fetcher._generate_wayback_urls(
            ris_url=(
                "https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
                "Abfrage=Bundesnormen&Gesetzesnummer=10001622&FassungVom=2017-01-01"
            ),
            fassung_vom="2017-01-01",
        )
        assert isinstance(urls, list), "Must return list of URLs to try"
        assert len(urls) >= 1, "Must have at least one URL to try"

"""Tests for the OGD API fetcher module.

These tests validate metadata fetching from the Austrian OGD API v2.6:
- POST request construction with form-urlencoded body
- Pagination (Seitennummer, DokumenteProSeite)
- Rate limiting (300ms between requests)
- OgdSearchResult.OgdDocumentResults.OgdDocumentReference[] wrapper unwrapping
- Inkrafttretensdatum → fassung_vom mapping
- GesamteRechtsvorschriftUrl extraction
- Aenderung field extraction
- GSN parameter handling (ABGB: 10001622)
- Error handling (non-200, malformed JSON, empty responses)
"""

import time

import pytest

from git_for_law_austria.fetcher import OGDFetcher, OGDPaginatedResult, OGDVersionItem


OGD_URL = "https://data.bka.gv.at/ris/api/v2.6/Bundesrecht"
ABGB_GSN = "10001622"


# ── Request construction tests ────────────────────────────────────────────────


class TestOGDFetcherRequestConstruction:
    """Tests for POST request construction to the OGD API."""

    def test_request_url(self):
        """Fetcher sends POST to the correct OGD API endpoint."""
        fetcher = OGDFetcher()
        assert fetcher.api_url == OGD_URL, "Fetcher must use the v2.6 Bundesrecht endpoint"

    def test_form_encoded_body_contains_applikation(self):
        """Request body must include Applikation=BrKons."""
        fetcher = OGDFetcher()
        body = fetcher._build_request_body(gsn=ABGB_GSN)
        assert "Applikation=BrKons" in body, "Body must contain Applikation=BrKons"

    def test_form_encoded_body_contains_gesetzesnummer(self):
        """Request body must include the Gesetzesnummer parameter."""
        fetcher = OGDFetcher()
        body = fetcher._build_request_body(gsn=ABGB_GSN)
        assert f"Gesetzesnummer={ABGB_GSN}" in body, (
            f"Body must contain Gesetzesnummer={ABGB_GSN}"
        )

    def test_form_encoded_body_is_url_encoded(self):
        """Request body must be URL-encoded form data."""
        fetcher = OGDFetcher()
        body = fetcher._build_request_body(gsn="10001622")
        assert "=" in body, "Body must be key=value format"
        assert " " not in body or "%20" in body, "Body must be URL-encoded"

    def test_content_type_header(self):
        """Request must set Content-Type to application/x-www-form-urlencoded."""
        fetcher = OGDFetcher()
        headers = fetcher._build_headers()
        assert headers["Content-Type"] == "application/x-www-form-urlencoded", (
            "Content-Type must be application/x-www-form-urlencoded"
        )


# ── Pagination tests ──────────────────────────────────────────────────────────


class TestOGDFetcherPagination:
    """Tests for pagination parameter handling."""

    def test_seitennummer_default(self):
        """Default Seitennummer should be 1 (first page)."""
        fetcher = OGDFetcher()
        body = fetcher._build_request_body(gsn=ABGB_GSN)
        assert "Seitennummer=1" in body, "Default Seitennummer must be 1"

    def test_seitennummer_custom(self):
        """Seitennummer must accept arbitrary integer values."""
        fetcher = OGDFetcher()
        body = fetcher._build_request_body(gsn=ABGB_GSN, page=3)
        assert "Seitennummer=3" in body, "Custom Seitennummer must be reflected in body"

    def test_dokumente_pro_seite_default(self):
        """Default DokumenteProSeite should be Ten."""
        fetcher = OGDFetcher()
        body = fetcher._build_request_body(gsn=ABGB_GSN)
        assert "DokumenteProSeite=Ten" in body, "Default DokumenteProSeite must be Ten"

    def test_dokumente_pro_seite_valid_values(self):
        """DokumenteProSeite must accept Ten, Twenty, Fifty, OneHundred."""
        fetcher = OGDFetcher()
        valid_values = ["Ten", "Twenty", "Fifty", "OneHundred"]
        for val in valid_values:
            body = fetcher._build_request_body(gsn=ABGB_GSN, per_page=val)
            assert f"DokumenteProSeite={val}" in body, (
                f"DokumenteProSeite must accept {val}"
            )

    def test_pagination_iterates_all_pages(self):
        """Fetcher must iterate through all pages until Seitennummer exceeds total."""
        fetcher = OGDFetcher()
        pages = list(fetcher._paginate(gsn=ABGB_GSN, per_page="OneHundred"))
        assert len(pages) > 0, "Must yield at least one page"
        page_numbers = [p for p, _ in pages]
        assert page_numbers == sorted(page_numbers), "Page numbers must be sequential"


# ── Rate limiting tests ───────────────────────────────────────────────────────


class TestOGDFetcherRateLimiting:
    """Tests for 300ms rate limiting between API requests."""

    def test_min_interval_property(self):
        """Fetcher must expose a min_interval attribute set to 300ms."""
        fetcher = OGDFetcher()
        assert fetcher.min_interval == 0.3, "min_interval must be 0.3 (300ms)"

    def test_rate_limit_delay_applied(self):
        """Successive calls to _rate_limited_request must respect min_interval."""
        fetcher = OGDFetcher()
        time.monotonic()
        fetcher._rate_limited_request("POST", OGD_URL, data="test")
        t1 = time.monotonic()
        fetcher._rate_limited_request("POST", OGD_URL, data="test")
        t2 = time.monotonic()
        elapsed = t2 - t1
        assert elapsed >= 0.28, (
            f"Rate limit delay must be at least ~300ms between requests, got {elapsed:.3f}s"
        )

    def test_rate_limit_can_be_configured(self):
        """User must be able to configure the rate limit interval."""
        fetcher = OGDFetcher(rate_limit=0.5)
        assert fetcher.min_interval == 0.5, "Rate limit must be configurable"


# ── OgdSearchResult wrapper unwrapping tests ───────────────────────────────────
#
# Real OGD API v2.6 returns:
# { "OgdSearchResult": { "OgdDocumentResults": { "OgdDocumentReference": [...] } } }


class TestOGDFetcherWrapperUnwrapping:
    """Tests for unwrapping OgdSearchResult.OgdDocumentResults.OgdDocumentReference[]."""

    def test_unwrap_extracts_ogd_document_reference_list(self, sample_ogd_page_1):
        """Fetcher must unwrap OgdSearchResult -> OgdDocumentResults -> OgdDocumentReference."""
        fetcher = OGDFetcher()
        references = fetcher._unwrap_references(sample_ogd_page_1)
        assert isinstance(references, list), "Unwrapped result must be a list"
        assert len(references) == 3, "Must extract all 3 OgdDocumentReference items"

    def test_unwrap_empty_references(self, sample_ogd_empty_response):
        """Empty OgdDocumentReference list must return empty list, not error."""
        fetcher = OGDFetcher()
        references = fetcher._unwrap_references(sample_ogd_empty_response)
        assert references == [], "Empty OgdDocumentReference must yield empty list"

    def test_unwrap_preserves_item_fields(self, sample_ogd_wrapper_response):
        """Unwrapping must preserve all item fields intact."""
        fetcher = OGDFetcher()
        references = fetcher._unwrap_references(sample_ogd_wrapper_response)
        assert len(references) == 1
        item = references[0]
        assert item["Inkrafttretensdatum"] == "2020-01-01"
        assert "GesamteRechtsvorschriftUrl" in item
        assert "Aenderung" in item

    def test_total_count_from_wrapper(self, sample_ogd_wrapper_response):
        """GesamtzahlErgebnisse is in OgdSearchResult, not at top level."""
        fetcher = OGDFetcher()
        total = fetcher._extract_total_count(sample_ogd_wrapper_response)
        assert total == 1, f"GesamtzahlErgebnisse must be 1, got {total}"

    def test_total_count_from_page_1(self, sample_ogd_page_1):
        """GesamtzahlErgebnisse extracted from OgdSearchResult wrapper."""
        fetcher = OGDFetcher()
        total = fetcher._extract_total_count(sample_ogd_page_1)
        assert total == 2556, f"GesamtzahlErgebnisse must be 2556, got {total}"


# ── Response parsing tests ────────────────────────────────────────────────────


class TestOGDFetcherResponseParsing:
    """Tests for parsing OGD API v2.6 JSON responses into structured items."""

    def test_parse_response_unwraps_and_parses(self, sample_ogd_page_1):
        """Parsed response must unwrap wrapper and parse each OgdDocumentReference."""
        fetcher = OGDFetcher()
        result = fetcher._parse_response(sample_ogd_page_1)
        assert isinstance(result, list), "Parsed result must be a list"
        assert len(result) == 3, "Must parse all 3 items from page 1"

    def test_parse_response_all_items_are_ogd_version_items(self, sample_ogd_page_1):
        """Each parsed item must be an OGDVersionItem."""
        fetcher = OGDFetcher()
        result = fetcher._parse_response(sample_ogd_page_1)
        for item in result:
            assert isinstance(item, OGDVersionItem), (
                f"Each item must be OGDVersionItem, got {type(item)}"
            )

    def test_parse_item_maps_inkrafttretensdatum_to_fassung_vom(self, sample_ogd_single_item):
        """Inkrafttretensdatum must be mapped to fassung_vom attribute."""
        fetcher = OGDFetcher()
        item = fetcher._parse_item(sample_ogd_single_item)
        assert item.fassung_vom == "2018-01-01", (
            "Inkrafttretensdatum '2018-01-01' must map to fassung_vom"
        )

    def test_parse_item_stores_aenderung(self, sample_ogd_single_item):
        """Parsed item must store the Aenderung (amendment) text."""
        fetcher = OGDFetcher()
        item = fetcher._parse_item(sample_ogd_single_item)
        assert item.aenderung == "BGBl. I Nr. 30/2018", (
            "Aenderung must match amendment reference"
        )

    def test_parse_item_extracts_gesamte_rechtsvorschrift_url(self, sample_ogd_single_item):
        """Parsed item must store GesamteRechtsvorschriftUrl for Wayback URL construction."""
        fetcher = OGDFetcher()
        item = fetcher._parse_item(sample_ogd_single_item)
        assert item.ris_url is not None, "GesamteRechtsvorschriftUrl must be stored"
        assert "GeltendeFassung" in item.ris_url, (
            "ris_url must contain GeltendeFassung"
        )
        assert "Gesetzesnummer=10001622" in item.ris_url, (
            "ris_url must contain the correct Gesetzesnummer"
        )
        assert "FassungVom=2018-01-01" in item.ris_url, (
            "ris_url must contain FassungVom date parameter"
        )

    def test_parse_item_extracts_sections(self, sample_ogd_single_item):
        """Parsed item must extract ArtikelParagraphAnlage entries."""
        fetcher = OGDFetcher()
        item = fetcher._parse_item(sample_ogd_single_item)
        assert len(item.sections) == 1, "Must extract one section entry"
        sec = item.sections[0]
        assert sec["section_type"] == "Paragraf"
        assert sec["section_id"] == "§ 531"
        assert sec["api_id"] == "F0531"

    def test_parse_item_handles_missing_apa(self):
        """Item with no ArtikelParagraphAnlage field must produce empty sections."""
        fetcher = OGDFetcher()
        item = {
            "Inkrafttretensdatum": "2020-01-01",
            "GesamteRechtsvorschriftUrl": "https://www.ris.bka.gv.at/...",
        }
        parsed = fetcher._parse_item(item)
        assert parsed.sections == [], "Missing APA must yield empty sections list"

    def test_parse_item_section_types(self):
        """Must correctly identify Paragraf, Artikel, and Anlage section types."""
        fetcher = OGDFetcher()
        item_data = {
            "Inkrafttretensdatum": "2020-01-01",
            "Aenderung": "Test",
            "GesamteRechtsvorschriftUrl": "https://www.ris.bka.gv.at/...",
            "ArtikelParagraphAnlage": [
                {"Typ": "Paragraf", "Bezeichnung": "§ 1", "Id": "F0001"},
                {"Typ": "Artikel", "Bezeichnung": "Art. 1", "Id": "A0001"},
                {"Typ": "Anlage", "Bezeichnung": "Anlage 1", "Id": "X0001"},
            ],
        }
        parsed = fetcher._parse_item(item_data)
        types = [s["section_type"] for s in parsed.sections]
        assert "Paragraf" in types
        assert "Artikel" in types
        assert "Anlage" in types


# ── GSN parameter tests ───────────────────────────────────────────────────────


class TestOGDFetcherGSN:
    """Tests for Gesetzesnummer handling."""

    def test_abgb_correct_gsn(self):
        """Fetcher must use GSN 10001622 for ABGB (not the incorrect 10001468)."""
        fetcher = OGDFetcher()
        body = fetcher._build_request_body(gsn="10001622")
        assert "Gesetzesnummer=10001622" in body, "ABGB GSN must be 10001622"
        assert "Gesetzesnummer=10001468" not in body, (
            "Must NOT use incorrect GSN 10001468"
        )

    def test_fetch_abgb_uses_correct_gsn(self):
        """The convenience method for fetching ABGB must use the correct GSN."""
        fetcher = OGDFetcher()
        assert fetcher.ABGB_GSN == "10001622", "ABGB_GSN constant must be 10001622"

    def test_fetch_by_gsn_accepts_arbitrary_gsn(self):
        """fetch_metadata must accept any GSN string, not just ABGB."""
        fetcher = OGDFetcher()
        body = fetcher._build_request_body(gsn="10000138")  # B-VG
        assert "Gesetzesnummer=10000138" in body, "Must accept B-VG GSN"


# ── Error handling tests ──────────────────────────────────────────────────────


class TestOGDFetcherErrors:
    """Tests for error handling in the OGD fetcher."""

    def test_non_200_response_raises(self):
        """Non-200 HTTP response must raise an appropriate error."""
        fetcher = OGDFetcher()
        mock_response = type("Response", (), {"status_code": 502, "text": "Bad Gateway"})()
        with pytest.raises(Exception) as exc_info:
            fetcher._handle_response(mock_response)
        assert "502" in str(exc_info.value) or "Bad Gateway" in str(exc_info.value), (
            "Error must mention status code or body"
        )

    def test_malformed_json_raises(self, sample_ogd_malformed_response):
        """Malformed JSON response must raise an appropriate error."""
        fetcher = OGDFetcher()
        with pytest.raises(Exception) as exc_info:
            fetcher._parse_response_text(sample_ogd_malformed_response)
        assert "json" in str(exc_info.value).lower() or "parse" in str(exc_info.value).lower(), (
            "Error must indicate a JSON parsing issue"
        )

    def test_empty_response_handled(self, sample_ogd_empty_response):
        """Empty OgdDocumentReference list must not raise; must return empty result."""
        fetcher = OGDFetcher()
        items = fetcher._parse_response(sample_ogd_empty_response)
        assert items == [], "Empty response must produce empty item list"

    def test_fetch_empty_law_returns_empty(self, sample_ogd_empty_response):
        """Fetching a law with no results must return empty version list."""
        fetcher = OGDFetcher()
        result = fetcher._parse_response(sample_ogd_empty_response)
        assert len(result) == 0, "No-result law must yield zero items"

    def test_parse_response_raises_on_missing_wrapper(self):
        """Response missing OgdSearchResult wrapper must raise clear error."""
        fetcher = OGDFetcher()
        bad_response = {"Ergebnis": []}  # old-format, no wrapper
        with pytest.raises(Exception) as exc_info:
            fetcher._parse_response(bad_response)
        assert "OgdSearchResult" in str(exc_info.value) or "wrapper" in str(exc_info.value).lower(), (
            "Error must indicate missing OgdSearchResult wrapper"
        )


# ── Paginated result integration tests ────────────────────────────────────────


class TestOGDFetcherPaginatedResult:
    """Tests for the OGDPaginatedResult dataclass."""

    def test_paginated_result_stores_items(self, sample_ogd_page_1):
        """OGDPaginatedResult must store parsed items."""
        result = OGDPaginatedResult(
            items=[OGDVersionItem() for _ in range(3)],
            total_count=2556,
            page=1,
        )
        assert len(result.items) == 3, "Must store 3 items"

    def test_paginated_result_has_more(self, sample_ogd_page_1):
        """OGDPaginatedResult must indicate whether more pages exist."""
        items_per_page = len(
            sample_ogd_page_1["OgdSearchResult"]["OgdDocumentResults"]["OgdDocumentReference"]
        )
        result = OGDPaginatedResult(
            items=[],
            total_count=2556,
            page=1,
            per_page=items_per_page,
        )
        assert result.has_more is True, (
            f"Total 2556 >> per_page {items_per_page}, must have more pages"
        )

    def test_paginated_result_last_page(self):
        """OGDPaginatedResult must indicate no more pages on the last page."""
        result = OGDPaginatedResult(
            items=[OGDVersionItem() for _ in range(10)],
            total_count=100,
            page=10,
            per_page=10,
        )
        assert result.has_more is False, "Last page must report has_more=False"


# ── Integration: fetch all versions ───────────────────────────────────────────


class TestOGDFetcherIntegration:
    """Integration-style tests for the full fetch flow."""

    def test_fetch_all_versions_returns_list(self, sample_ogd_all_versions):
        """fetch_all_versions must return a list of OGDVersionItem objects."""
        fetcher = OGDFetcher()
        result = fetcher._parse_response(
            {"OgdSearchResult": {"OgdDocumentResults": {"OgdDocumentReference": sample_ogd_all_versions}}}
        )
        assert isinstance(result, list), "Result must be a list"
        assert len(result) == len(sample_ogd_all_versions), (
            f"Must return {len(sample_ogd_all_versions)} items"
        )

    def test_fetch_all_versions_correct_type(self, sample_ogd_all_versions):
        """Each returned item must be an OGDVersionItem."""
        fetcher = OGDFetcher()
        items = fetcher._parse_response(
            {"OgdSearchResult": {"OgdDocumentResults": {"OgdDocumentReference": sample_ogd_all_versions}}}
        )
        for item in items:
            assert isinstance(item, OGDVersionItem), (
                f"Each item must be OGDVersionItem, got {type(item)}"
            )

    def test_fetch_all_versions_unique_fassung_vom(self, sample_ogd_all_versions):
        """The fetcher must be able to report unique fassung_vom dates."""
        fetcher = OGDFetcher()
        fassung_dates = fetcher._extract_unique_dates(sample_ogd_all_versions)
        expected_unique = len({v["Inkrafttretensdatum"] for v in sample_ogd_all_versions})
        assert len(fassung_dates) == expected_unique, (
            f"Must extract {expected_unique} unique fassung_vom dates"
        )

    def test_duplicate_aenderung_different_dates(self, sample_ogd_duplicate_aenderung):
        """Two versions with identical Aenderung but different Inkrafttretensdatum must both be kept."""
        fetcher = OGDFetcher()
        items = fetcher._parse_response(
            {"OgdSearchResult": {"OgdDocumentResults": {"OgdDocumentReference": sample_ogd_duplicate_aenderung}}}
        )
        assert len(items) == 2, "Both items must be retained despite duplicate Aenderung"
        dates = [item.fassung_vom for item in items]
        assert "2015-01-01" in dates
        assert "2015-06-01" in dates

    def test_ris_url_present_in_all_items(self, sample_ogd_all_versions):
        """Every OGDVersionItem must have a ris_url from GesamteRechtsvorschriftUrl."""
        fetcher = OGDFetcher()
        items = fetcher._parse_response(
            {"OgdSearchResult": {"OgdDocumentResults": {"OgdDocumentReference": sample_ogd_all_versions}}}
        )
        for item in items:
            assert item.ris_url, "Every item must have a non-empty ris_url"
            assert item.ris_url.startswith("https://"), "ris_url must be HTTPS"

    def test_inkrafttretensdatum_always_present(self, sample_ogd_all_versions):
        """Every parsed item must have a fassung_vom derived from Inkrafttretensdatum."""
        fetcher = OGDFetcher()
        items = fetcher._parse_response(
            {"OgdSearchResult": {"OgdDocumentResults": {"OgdDocumentReference": sample_ogd_all_versions}}}
        )
        for item in items:
            assert item.fassung_vom, "fassung_vom must not be empty"
            assert len(item.fassung_vom) == 10, (
                f"fassung_vom must be YYYY-MM-DD format, got {item.fassung_vom}"
            )

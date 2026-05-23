"""Tests for the OGD content fetcher module.

These tests validate downloading and parsing of full legal text from the
RIS Dokument XML endpoint, which is NOT blocked by Myra WAF:

- POST data.bka.gv.at/ris/api/v2.6/Bundesrecht with FassungVom={date}
  returns NOR IDs and ContentReference URLs
- GET www.ris.bka.gv.at/Dokumente/Bundesnormen/{NOR_ID}/{NOR_ID}.xml
  returns structured XML with full legal text
- XML structure: <absatz typ="abs" ct="text"> = legal paragraphs,
  <gldsym> = section symbols, <ueberschrift typ="g1|g2|para" ct="text"> = headings
- Rate limits: 300ms between requests
- Stammnorm (§ 0): metadata only, no legal text — must be skipped
"""

from unittest import mock

import pytest

from git_for_law_austria.ogd_content_fetcher import OGDContentFetcher, NORContent


OGD_SEARCH_URL = "https://data.bka.gv.at/ris/api/v2.6/Bundesrecht"
DOKUMENT_BASE_URL = "https://www.ris.bka.gv.at/Dokumente/Bundesnormen"


# ── Sample OGD search response with NOR IDs ─────────────────────────────────


@pytest.fixture
def sample_search_response_with_nor_ids():
    """OGD search response including NOR IDs and ContentReference URLs.

    Real OGD API returns ContentReference URLs that link to the Dokument XML.
    """
    return {
        "OgdSearchResult": {
            "OgdDocumentResults": {
                "OgdDocumentReference": [
                    {
                        "Inkrafttretensdatum": "2017-01-01",
                        "Aenderung": "BGBl. I Nr. 43/2016",
                        "ContentReference": (
                            "https://www.ris.bka.gv.at/Dokumente/Bundesnormen/"
                            "NOR40198929/NOR40198929.xml"
                        ),
                        "ArtikelParagraphAnlage": [
                            {"Typ": "Paragraf", "Bezeichnung": "§ 1", "Id": "F0025"},
                            {"Typ": "Paragraf", "Bezeichnung": "§ 2", "Id": "F0026"},
                        ],
                    },
                    {
                        "Inkrafttretensdatum": "2017-01-01",
                        "Aenderung": "BGBl. I Nr. 43/2016",
                        "ContentReference": (
                            "https://www.ris.bka.gv.at/Dokumente/Bundesnormen/"
                            "NOR40198930/NOR40198930.xml"
                        ),
                        "ArtikelParagraphAnlage": [
                            {"Typ": "Paragraf", "Bezeichnung": "§ 15", "Id": "F0040"},
                        ],
                    },
                ]
            },
            "GesamtzahlErgebnisse": 2,
        }
    }


@pytest.fixture
def sample_search_response_stammnorm():
    """OGD search response containing only Stammnorm (§ 0).

    Stammnorm is 358KB of metadata only — no legal text.
    """
    return {
        "OgdSearchResult": {
            "OgdDocumentResults": {
                "OgdDocumentReference": [
                    {
                        "Inkrafttretensdatum": "2017-01-01",
                        "Aenderung": "Stammnorm",
                        "ContentReference": (
                            "https://www.ris.bka.gv.at/Dokumente/Bundesnormen/"
                            "NOR40000000/NOR40000000.xml"
                        ),
                        "ArtikelParagraphAnlage": [
                            {"Typ": "Paragraf", "Bezeichnung": "§ 0", "Id": "F0000"},
                        ],
                    }
                ]
            },
            "GesamtzahlErgebnisse": 1,
        }
    }


@pytest.fixture
def sample_search_response_empty():
    """OGD search response with no NOR references."""
    return {
        "OgdSearchResult": {
            "OgdDocumentResults": {
                "OgdDocumentReference": []
            },
            "GesamtzahlErgebnisse": 0,
        }
    }


# ── Sample Dokument XML ────────────────────────────────────────────────────


@pytest.fixture
def sample_dokument_xml_single_paragraph():
    """Dokument XML for a single paragraph (§ 1) with absatz elements."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<dokument>
  <absatz typ="abs" ct="text">(1) Jeder Mensch hat von seiner Geburt an
angeborene Rechte und ist daher als eine Person zu betrachten.</absatz>
  <absatz typ="abs" ct="text">(2) Niemand darf durch Vertrag oder Gesetz
in diesen Rechten eingeschr&auml;nkt werden.</absatz>
</dokument>"""


@pytest.fixture
def sample_dokument_xml_with_heading():
    """Dokument XML with gldsym and ueberschrift heading elements."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<dokument>
  <gldsym>&sect; 1.</gldsym>
  <ueberschrift typ="g1" ct="text">Allgemeiner Teil</ueberschrift>
  <absatz typ="abs" ct="text">(1) Die allgemeinen Grunds&auml;tze des
Rechts gelten f&uuml;r jedermann.</absatz>
  <absatz typ="abs" ct="text">(2) Erg&auml;nzende Bestimmungen finden sich
in den Nebengesetzen.</absatz>
</dokument>"""


@pytest.fixture
def sample_dokument_xml_multi_section():
    """Dokument XML with multiple sections and sub-headings."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<dokument>
  <gldsym>&sect; 16</gldsym>
  <ueberschrift typ="g1" ct="text">I. Allgemeines</ueberschrift>
  <absatz typ="abs" ct="text">(1) Jeder Mensch hat angeborene, schon durch
die Vernunft einleuchtende Rechte.</absatz>
  <absatz typ="abs" ct="text">(2) Sklaven haben keine Rechte.</absatz>
  <ueberschrift typ="g2" ct="text">II. Besonderer Teil</ueberschrift>
  <absatz typ="abs" ct="text">Dieser Paragraph wurde mehrfach novelliert.</absatz>
</dokument>"""


@pytest.fixture
def sample_dokument_xml_with_umlauts():
    """Dokument XML with German umlauts and special characters."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<dokument>
  <gldsym>&sect; 1</gldsym>
  <absatz typ="abs" ct="text">Die &Uuml;bergangsbestimmungen finden sich in
der &Auml;nderung des B&uuml;rgerlichen Gesetzbuches.</absatz>
  <absatz typ="abs" ct="text">&Ouml;sterreichisches Recht gilt f&uuml;r alle
Bundesl&ouml;nder und L&auml;nder.</absatz>
</dokument>"""


@pytest.fixture
def sample_dokument_xml_empty():
    """Dokument XML with no absatz elements."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<dokument>
  <gldsym>&sect; 99</gldsym>
</dokument>"""


@pytest.fixture
def sample_dokument_xml_stammnorm():
    """Dokument XML for Stammnorm (§ 0) — 358KB metadata only, no legal text."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<dokument>
  <gldsym>&sect; 0</gldsym>
  <ueberschrift typ="g1" ct="text">Stammnorm</ueberschrift>
  <metadaten>
    <stammdaten>...</stammdaten>
  </metadaten>
</dokument>"""


@pytest.fixture
def sample_dokument_xml_with_article():
    """Dokument XML for an Artikel (Art. 2) section."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<dokument>
  <gldsym>Art. 2</gldsym>
  <ueberschrift typ="g1" ct="text">Inkrafttreten</ueberschrift>
  <absatz typ="abs" ct="text">(1) Dieses Bundesgesetz tritt mit 1. J&auml;nner 2017
in Kraft.</absatz>
  <absatz typ="abs" ct="text">(2) Mit dem Inkrafttreten treten alle
entgegenstehenden Bestimmungen au&szlig;er Kraft.</absatz>
</dokument>"""


# ── NOR ID extraction tests ─────────────────────────────────────────────────


class TestNORIDExtraction:
    """Tests for extracting NOR IDs from OGD search API responses."""

    def test_extracts_nor_ids_from_content_reference(self, sample_search_response_with_nor_ids):
        """NOR IDs must be extracted from ContentReference URLs in the search response."""
        fetcher = OGDContentFetcher()
        nor_ids = fetcher._extract_nor_ids(sample_search_response_with_nor_ids)
        assert isinstance(nor_ids, list), "Must return a list"
        assert len(nor_ids) == 2, "Must extract 2 NOR IDs"
        assert "NOR40198929" in nor_ids, "Must contain NOR40198929"
        assert "NOR40198930" in nor_ids, "Must contain NOR40198930"

    def test_extract_nor_ids_handles_empty_response(self, sample_search_response_empty):
        """Empty search response must return empty NOR ID list."""
        fetcher = OGDContentFetcher()
        nor_ids = fetcher._extract_nor_ids(sample_search_response_empty)
        assert nor_ids == [], "Empty response must yield empty NOR ID list"

    def test_extract_nor_ids_deduplicates(self):
        """Duplicate NOR IDs in the response must be deduplicated."""
        fetcher = OGDContentFetcher()
        response = {
            "OgdSearchResult": {
                "OgdDocumentResults": {
                    "OgdDocumentReference": [
                        {
                            "ContentReference": (
                                "https://www.ris.bka.gv.at/Dokumente/Bundesnormen/"
                                "NOR40198929/NOR40198929.xml"
                            ),
                            "ArtikelParagraphAnlage": [],
                            "Inkrafttretensdatum": "2017-01-01",
                        },
                        {
                            "ContentReference": (
                                "https://www.ris.bka.gv.at/Dokumente/Bundesnormen/"
                                "NOR40198929/NOR40198929.xml"
                            ),
                            "ArtikelParagraphAnlage": [],
                            "Inkrafttretensdatum": "2017-01-01",
                        },
                    ]
                }
            }
        }
        nor_ids = fetcher._extract_nor_ids(response)
        assert len(nor_ids) == 1, "Duplicate NOR IDs must be deduplicated"

    def test_extract_nor_ids_handles_missing_content_reference(self):
        """Items without ContentReference must be skipped gracefully."""
        fetcher = OGDContentFetcher()
        response = {
            "OgdSearchResult": {
                "OgdDocumentResults": {
                    "OgdDocumentReference": [
                        {
                            "Inkrafttretensdatum": "2017-01-01",
                            "ArtikelParagraphAnlage": [],
                        }
                    ]
                }
            }
        }
        nor_ids = fetcher._extract_nor_ids(response)
        assert nor_ids == [], "Missing ContentReference must produce empty list"


# ── Dokument XML URL construction tests ─────────────────────────────────────


class TestDokumentURLConstruction:
    """Tests for constructing Dokument XML URLs from NOR IDs."""

    def test_build_url_from_nor_id(self):
        """Given NOR40198929, URL must be the correct Dokument XML endpoint."""
        fetcher = OGDContentFetcher()
        url = fetcher._build_dokument_url("NOR40198929")
        expected = (
            "https://www.ris.bka.gv.at/Dokumente/Bundesnormen/"
            "NOR40198929/NOR40198929.xml"
        )
        assert url == expected, f"URL must be {expected}"

    def test_build_url_different_nor_ids(self):
        """URL construction must work for any NOR ID."""
        fetcher = OGDContentFetcher()
        test_ids = [
            ("NOR40198929", "NOR40198929/NOR40198929.xml"),
            ("NOR40198930", "NOR40198930/NOR40198930.xml"),
            ("NOR50000001", "NOR50000001/NOR50000001.xml"),
        ]
        for nor_id, expected_suffix in test_ids:
            url = fetcher._build_dokument_url(nor_id)
            assert url.endswith(expected_suffix), (
                f"URL for {nor_id} must end with {expected_suffix}"
            )
            assert url.startswith(DOKUMENT_BASE_URL), (
                f"URL must start with {DOKUMENT_BASE_URL}"
            )

    def test_build_url_always_https(self):
        """Dokument XML URLs must use HTTPS."""
        fetcher = OGDContentFetcher()
        url = fetcher._build_dokument_url("NOR40198929")
        assert url.startswith("https://"), "Dokument URL must use HTTPS"


# ── XML parsing tests ───────────────────────────────────────────────────────


class TestDokumentXMLParsing:
    """Tests for parsing RIS Dokument XML into structured text."""

    def test_parses_absatz_elements(self, sample_dokument_xml_single_paragraph):
        """<absatz typ="abs" ct="text"> elements must be extracted as body text."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_single_paragraph)
        assert result.body, "Body must not be empty"
        assert "angeborene Rechte" in result.body, "Must contain first absatz text"
        assert "eingeschr" in result.body, "Must contain second absatz text"

    def test_parses_gldsym_element(self, sample_dokument_xml_with_heading):
        """<gldsym> section symbols must be extracted as heading."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_with_heading)
        assert result.heading, "Heading must not be empty"
        assert "§ 1" in result.heading, "Must contain section symbol"

    def test_parses_ueberschrift_elements(self, sample_dokument_xml_with_heading):
        """<ueberschrift> headings must be included in body or heading."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_with_heading)
        assert "Allgemeiner Teil" in result.body, (
            "ueberschrift text must appear in body"
        )

    def test_parses_multi_section_xml(self, sample_dokument_xml_multi_section):
        """Multi-section XML with sub-headings must be fully parsed."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_multi_section)
        assert "I. Allgemeines" in result.body, "Must include g1 heading"
        assert "II. Besonderer Teil" in result.body, "Must include g2 heading"
        assert "angeborene" in result.body, "Must include absatz text"
        assert "mehrfach novelliert" in result.body, "Must include later absatz text"

    def test_parses_article_xml(self, sample_dokument_xml_with_article):
        """Artikel XML with ueberschrift and absatz must parse correctly."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_with_article)
        assert result.heading == "Art. 2", "Heading must be Art. 2"
        assert "Inkrafttreten" in result.body, "Must include ueberschrift text"
        assert "Bundesgesetz tritt" in result.body, "Must include absatz text"

    def test_parse_returns_nor_content_object(self, sample_dokument_xml_single_paragraph):
        """_parse_dokument_xml must return a NORContent dataclass/object."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_single_paragraph)
        assert isinstance(result, NORContent), (
            f"Must return NORContent, got {type(result)}"
        )

    def test_nor_content_has_required_fields(self, sample_dokument_xml_with_heading):
        """NORContent must have nor_id, heading, body, and section_type fields."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(
            sample_dokument_xml_with_heading, nor_id="NOR40198929"
        )
        assert result.nor_id == "NOR40198929", "nor_id must be stored"
        assert result.heading is not None, "heading must be present"
        assert result.body is not None, "body must be present"
        assert hasattr(result, "section_type"), "must have section_type field"


# ── Body text assembly tests ────────────────────────────────────────────────


class TestBodyTextAssembly:
    """Tests for assembling extracted text elements into a body string."""

    def test_elements_joined_with_newlines(self, sample_dokument_xml_single_paragraph):
        """Extracted text elements must be joined with newlines for readability."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_single_paragraph)
        assert "\n" in result.body, "Multi-element body must contain newlines"

    def test_body_no_leading_trailing_whitespace(self, sample_dokument_xml_single_paragraph):
        """Assembled body must not have leading or trailing whitespace."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_single_paragraph)
        assert not result.body.startswith("\n"), "Body must not start with newline"
        assert not result.body.endswith("\n"), "Body must not end with newline"

    def test_html_entities_decoded_in_body(self, sample_dokument_xml_with_umlauts):
        """HTML entities (&Uuml;, &auml;, etc.) must be decoded to Unicode."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_with_umlauts)
        assert "&Uuml;" not in result.body, "HTML entities must be decoded"
        assert "&Auml;" not in result.body, "HTML entities must be decoded"
        assert "Übergangsbestimmungen" in result.body, (
            "&Uuml; must decode to Ü"
        )

    def test_body_eq_umlauts_decoded(self, sample_dokument_xml_with_umlauts):
        """Umlaut entities (&uuml;, &auml;, &ouml;) must decode correctly."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_with_umlauts)
        assert "Österreichisches" in result.body, "&Ouml; must decode to Ö"
        assert "Bundeslönder" in result.body, "&ouml; must decode to ö"
        assert "für" in result.body, "&uuml; must decode to ü"

    def test_paragraph_symbol_decoded(self, sample_dokument_xml_with_heading):
        """&sect; entity must be decoded to §."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_with_heading)
        assert "&sect;" not in result.heading, "&sect; must be decoded"
        assert "§" in result.heading, "Heading must contain § symbol"

    def test_whitespace_inside_elements_normalized(self):
        """Extra whitespace within XML elements must be normalized."""
        xml = """<?xml version="1.0"?><dokument>
  <absatz typ="abs" ct="text">   Text   mit    vielen     Leerzeichen   </absatz>
</dokument>"""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(xml)
        assert "   " not in result.body, "Multiple spaces must be collapsed"
        assert "Text mit vielen Leerzeichen" in result.body, (
            "Whitespace must be normalized"
        )


# ── Stammnorm skip tests ────────────────────────────────────────────────────


class TestStammnormSkip:
    """Tests for skipping Stammnorm (§ 0) — metadata only, no legal text."""

    def test_identifies_stammnorm_by_paragraph_zero(self, sample_dokument_xml_stammnorm):
        """§ 0 / Stammnorm must be identified and skipped."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_stammnorm)
        assert result.body == "", "Stammnorm must yield empty body"

    def test_stammnorm_has_no_absatz_elements(self, sample_dokument_xml_stammnorm):
        """Stammnorm XML has no absatz elements — must return empty body."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_stammnorm)
        assert len(result.body) == 0, "Stammnorm body must be empty string"

    def test_is_stammnorm_method(self):
        """_is_stammnorm must detect § 0 section IDs."""
        fetcher = OGDContentFetcher()
        assert fetcher._is_stammnorm("§ 0") is True, "§ 0 must be identified as Stammnorm"
        assert fetcher._is_stammnorm("§_0") is True, "§_0 must be identified as Stammnorm"
        assert fetcher._is_stammnorm("§ 1") is False, "§ 1 must NOT be Stammnorm"
        assert fetcher._is_stammnorm("Art. 0") is False, "Art. 0 must NOT be Stammnorm"

    def test_stammnorm_nor_id_detected(self, sample_search_response_stammnorm):
        """Stammnorm NOR IDs must be detected and excluded from fetch."""
        fetcher = OGDContentFetcher()
        nor_ids = fetcher._extract_nor_ids(sample_search_response_stammnorm)
        assert "NOR40000000" in nor_ids, "Must extract the NOR ID"
        assert fetcher._is_stammnorm("§ 0") is True, (
            "Stammnorm section ID must be detected"
        )


# ── HTTP error fallback tests ───────────────────────────────────────────────


class TestDokumentHTTPErrors:
    """Tests for graceful HTTP error handling when fetching Dokument XML."""

    def test_404_returns_empty_body(self):
        """HTTP 404 must return NORContent with empty body, not raise."""
        fetcher = OGDContentFetcher()
        with mock.patch.object(fetcher, "_http_get") as mock_get:
            mock_response = mock.Mock(status_code=404, text="")
            mock_get.return_value = mock_response
            result = fetcher._fetch_dokument("NOR40198929")
            assert result.body == "", "404 must yield empty body"

    def test_500_returns_empty_body(self):
        """HTTP 500 must return NORContent with empty body, not raise."""
        fetcher = OGDContentFetcher()
        with mock.patch.object(fetcher, "_http_get") as mock_get:
            mock_response = mock.Mock(status_code=500, text="Internal Server Error")
            mock_get.return_value = mock_response
            result = fetcher._fetch_dokument("NOR40198929")
            assert result.body == "", "500 must yield empty body"

    def test_timeout_returns_empty_body(self):
        """HTTP timeout must return empty body, not raise."""
        import requests

        fetcher = OGDContentFetcher(timeout=5)
        with mock.patch.object(fetcher, "_http_get") as mock_get:
            mock_get.side_effect = requests.exceptions.Timeout("Connection timed out")
            result = fetcher._fetch_dokument("NOR40198929")
            assert result.body == "", "Timeout must yield empty body"

    def test_connection_error_returns_empty_body(self):
        """Connection errors must return empty body, not crash."""
        import requests

        fetcher = OGDContentFetcher()
        with mock.patch.object(fetcher, "_http_get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectionError(
                "Connection refused"
            )
            result = fetcher._fetch_dokument("NOR40198929")
            assert result.body == "", "Connection error must yield empty body"

    def test_error_does_not_crash_batch_fetch(self):
        """One failing NOR must not crash the entire batch fetch."""
        import requests

        fetcher = OGDContentFetcher()
        with mock.patch.object(fetcher, "_http_get") as mock_get:
            mock_get.side_effect = [
                requests.exceptions.Timeout(),
                mock.Mock(status_code=200, text="""<?xml version="1.0"?><dokument>
                    <gldsym>§ 2</gldsym>
                    <absatz typ="abs" ct="text">Gültiger Text.</absatz>
                    </dokument>"""),
            ]
            results = fetcher._fetch_multiple_dokumente(
                ["NOR40198929", "NOR40198930"]
            )
            assert len(results) == 2, "Must return results for all NOR IDs"
            assert results[0].body == "", "Failed NOR must have empty body"
            assert results[1].body != "", "Successful NOR must have content"


# ── Rate limiting tests ─────────────────────────────────────────────────────


class TestOGDContentRateLimiting:
    """Tests for 300ms rate limiting between Dokument XML requests."""

    def test_min_interval_default(self):
        """Default rate limit must be 300ms."""
        fetcher = OGDContentFetcher()
        assert fetcher.min_interval == 0.3, "min_interval must default to 0.3 (300ms)"

    def test_rate_limit_configurable(self):
        """Rate limit must be configurable on init."""
        fetcher = OGDContentFetcher(rate_limit=0.5)
        assert fetcher.min_interval == 0.5, "Rate limit must be configurable"

    def test_rate_limit_delay_between_requests(self):
        """Successive _rate_limited_get calls must respect min_interval."""
        import time

        fetcher = OGDContentFetcher()
        url = "https://www.ris.bka.gv.at/Dokumente/Bundesnormen/NOR40198929/NOR40198929.xml"
        with mock.patch.object(fetcher, "_raw_http_get") as mock_raw:
            mock_raw.return_value = mock.Mock(status_code=200, text="<dokument/>")
            fetcher._rate_limited_get(url)
            t1 = time.monotonic()
            fetcher._rate_limited_get(url)
            t2 = time.monotonic()
            elapsed = t2 - t1
            assert elapsed >= 0.28, (
                f"Rate limit delay must be at least ~300ms, got {elapsed:.3f}s"
            )

    def test_rate_limit_not_applied_on_first_request(self):
        """First request must not be delayed (no prior timestamp)."""
        import time

        fetcher = OGDContentFetcher()
        url = "https://www.ris.bka.gv.at/Dokumente/Bundesnormen/NOR40198929/NOR40198929.xml"
        with mock.patch.object(fetcher, "_raw_http_get") as mock_raw:
            mock_raw.return_value = mock.Mock(status_code=200, text="<dokument/>")
            start = time.monotonic()
            fetcher._rate_limited_get(url)
            elapsed = time.monotonic() - start
            assert elapsed < 0.3, (
                f"First request must not be delayed, took {elapsed:.3f}s"
            )


# ── Full fetch flow tests ───────────────────────────────────────────────────


class TestFullFetchFlow:
    """Tests for the end-to-end fetch flow: search API → NOR IDs → Dokument XML."""

    def test_fetch_law_content_end_to_end(self, sample_search_response_with_nor_ids):
        """End-to-end: search response → NOR IDs → parsed Dokument XML."""
        fetcher = OGDContentFetcher()
        with mock.patch.object(fetcher, "_search_ogd") as mock_search, \
             mock.patch.object(fetcher, "_fetch_dokument") as mock_fetch:
            mock_search.return_value = sample_search_response_with_nor_ids
            mock_fetch.side_effect = lambda nor_id: NORContent(
                nor_id=nor_id,
                heading=f"§ {nor_id[-2:]}",
                body=f"Text for {nor_id}.",
                section_type="Paragraf",
            )
            results = fetcher.fetch_law_content(gsn="10001622", fassung_vom="2017-01-01")
            assert isinstance(results, list), "Must return a list"
            assert len(results) == 2, "Must fetch content for 2 NOR IDs"
            for r in results:
                assert isinstance(r, NORContent), (
                    f"Each result must be NORContent, got {type(r)}"
                )

    def test_fetch_law_content_passes_fassung_vom_to_search(self):
        """fassung_vom must be passed to the search API as FassungVom parameter."""
        fetcher = OGDContentFetcher()
        with mock.patch.object(fetcher, "_search_ogd") as mock_search:
            mock_search.return_value = {
                "OgdSearchResult": {
                    "OgdDocumentResults": {"OgdDocumentReference": []},
                    "GesamtzahlErgebnisse": 0,
                }
            }
            fetcher.fetch_law_content(gsn="10001622", fassung_vom="2017-01-01")
            call_args = mock_search.call_args
            assert "FassungVom" in str(call_args), (
                "Search must include FassungVom parameter"
            )
            assert "2017-01-01" in str(call_args), (
                "Search must pass the correct fassung_vom date"
            )

    def test_fetch_law_content_returns_empty_list_for_no_results(self):
        """When the search returns no NOR IDs, must return empty list."""
        fetcher = OGDContentFetcher()
        with mock.patch.object(fetcher, "_search_ogd") as mock_search:
            mock_search.return_value = {
                "OgdSearchResult": {
                    "OgdDocumentResults": {"OgdDocumentReference": []},
                    "GesamtzahlErgebnisse": 0,
                }
            }
            results = fetcher.fetch_law_content(gsn="10001622", fassung_vom="1812-01-01")
            assert results == [], "No search results must yield empty list"

    def test_fetch_law_content_respects_max_sections(self, sample_search_response_with_nor_ids):
        """max_sections parameter must limit the number of NOR documents fetched."""
        fetcher = OGDContentFetcher()
        with mock.patch.object(fetcher, "_search_ogd") as mock_search, \
             mock.patch.object(fetcher, "_fetch_dokument") as mock_fetch:
            mock_search.return_value = sample_search_response_with_nor_ids

            def _fake_fetch(nor_id):
                return NORContent(
                    nor_id=nor_id, heading="§ 1", body="Text", section_type="Paragraf"
                )

            mock_fetch.side_effect = _fake_fetch
            results = fetcher.fetch_law_content(
                gsn="10001622", fassung_vom="2017-01-01", max_sections=1
            )
            assert len(results) == 1, "max_sections=1 must limit to 1 result"

    def test_fetch_and_parse_full_pipeline(self, sample_dokument_xml_with_heading):
        """Full pipeline: fetch Dokument XML → parse → NORContent with text."""
        fetcher = OGDContentFetcher()
        with mock.patch.object(fetcher, "_http_get") as mock_http:
            mock_http.return_value = mock.Mock(
                status_code=200, text=sample_dokument_xml_with_heading
            )
            result = fetcher._fetch_dokument("NOR40198929")
            assert result.nor_id == "NOR40198929"
            assert "Allgemeiner Teil" in result.body
            assert "§ 1" in result.heading
            assert result.section_type == "Paragraf"


# ── Empty content handling tests ────────────────────────────────────────────


class TestEmptyContentHandling:
    """Tests for handling Dokument XML with no content."""

    def test_xml_with_no_absatz_returns_empty_body(self, sample_dokument_xml_empty):
        """XML with no absatz elements must return empty body string."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_empty)
        assert result.body == "", "No absatz elements must yield empty body"

    def test_empty_xml_returns_nor_content(self):
        """Empty XML must return a NORContent object, not None."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml("<dokument></dokument>")
        assert isinstance(result, NORContent), "Must return NORContent even for empty XML"

    def test_empty_xml_body_is_empty_string_not_none(self, sample_dokument_xml_empty):
        """Body must be '' not None for empty XML."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_empty)
        assert result.body == "", "Empty body must be empty string, not None"
        assert result.body is not None, "Body must not be None"

    def test_non_xml_response_handled(self):
        """Non-XML response body must return empty NORContent."""
        fetcher = OGDContentFetcher()
        with mock.patch.object(fetcher, "_http_get") as mock_http:
            mock_http.return_value = mock.Mock(
                status_code=200, text="<html><body>Not XML</body></html>"
            )
            result = fetcher._fetch_dokument("NOR40198929")
            assert isinstance(result, NORContent), "Must return NORContent"
            assert result.body == "", "Non-XML response must yield empty body"

    def test_whitespace_only_xml_returns_empty_body(self):
        """XML with only whitespace between tags must return empty body."""
        xml = """<?xml version="1.0"?><dokument>
        </dokument>"""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(xml)
        assert result.body == "", "Whitespace-only XML must yield empty body"


# ── Encoding tests ──────────────────────────────────────────────────────────


class TestEncodingHandling:
    """Tests for handling German legal text encoding."""

    def test_umlauts_survive_roundtrip(self, sample_dokument_xml_with_umlauts):
        """German umlauts must survive the parse roundtrip intact."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_with_umlauts)
        assert "Ü" in result.body, "Ü must survive roundtrip"
        assert "Ä" in result.body, "Ä must survive roundtrip"
        assert "Ö" in result.body, "Ö must survive roundtrip"
        assert "ü" in result.body, "ü must survive roundtrip"
        assert "ä" in result.body, "ä must survive roundtrip"
        assert "ö" in result.body, "ö must survive roundtrip"

    def test_section_symbol_survives_roundtrip(self, sample_dokument_xml_with_heading):
        """§ symbol must survive the parse roundtrip."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_with_heading)
        assert "§" in result.heading, "§ must survive roundtrip"

    def test_sharp_s_handled(self):
        """Eszett/ß must be handled correctly."""
        xml = """<?xml version="1.0"?><dokument>
          <absatz typ="abs" ct="text">Der &szlig;e Absatz enth&auml;lt ein &szlig;.</absatz>
        </dokument>"""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(xml)
        assert "ß" in result.body, "ß must be decoded correctly"

    def test_numeric_xml_entities_decoded(self):
        """Numeric XML entities (&#...;) must be decoded."""
        xml = """<?xml version="1.0"?><dokument>
          <absatz typ="abs" ct="text">Paragraph &#167; 1</absatz>
        </dokument>"""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(xml)
        assert "§" in result.body, "Numeric entity &#167; must decode to §"

    def test_mixed_encoding_survives_roundtrip(self):
        """Mixed umlauts, §, ß, and accented characters must all decode."""
        xml = """<?xml version="1.0"?><dokument>
          <absatz typ="abs" ct="text">&sect; 1 &Uuml;ber &Ouml;sterreichs
Rechts&uuml;berleitung f&uuml;r B&uuml;rger &auml;hnlicher
Staaten mit &szlig;-Sonderzeichen.</absatz>
        </dokument>"""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(xml)
        assert "§ 1 Über Österreichs Rechtsüberleitung für Bürger ähnlicher Staaten mit ß-Sonderzeichen." in result.body, (
            "All entities must decode correctly"
        )


# ── NORContent dataclass tests ──────────────────────────────────────────────


class TestNORContentDataclass:
    """Tests for the NORContent dataclass structure."""

    def test_nor_content_creation(self):
        """NORContent must be instantiable with all fields."""
        content = NORContent(
            nor_id="NOR40198929",
            heading="§ 1",
            body="Legal text here.",
            section_type="Paragraf",
        )
        assert content.nor_id == "NOR40198929"
        assert content.heading == "§ 1"
        assert content.body == "Legal text here."
        assert content.section_type == "Paragraf"

    def test_nor_content_defaults(self):
        """NORContent must have sensible defaults."""
        content = NORContent()
        assert content.nor_id == "", "Default nor_id must be empty string"
        assert content.body == "", "Default body must be empty string"

    def test_nor_content_equality(self):
        """Two NORContent with same fields must be equal."""
        a = NORContent(nor_id="X", heading="§ 1", body="Text", section_type="Paragraf")
        b = NORContent(nor_id="X", heading="§ 1", body="Text", section_type="Paragraf")
        assert a == b, "Identical NORContent must be equal"

    def test_nor_content_inequality(self):
        """NORContent with different bodies must not be equal."""
        a = NORContent(nor_id="X", body="Text A")
        b = NORContent(nor_id="X", body="Text B")
        assert a != b, "Different bodies must make NORContent unequal"


# ── Search API request construction tests ───────────────────────────────────


class TestSearchAPIRequestConstruction:
    """Tests for the OGD search API POST request with FassungVom parameter."""

    def test_search_request_includes_fassung_vom(self):
        """Search request must include FassungVom as a form parameter."""
        fetcher = OGDContentFetcher()
        with mock.patch.object(fetcher, "_http_post") as mock_post:
            mock_post.return_value = mock.Mock(
                status_code=200,
                text='{"OgdSearchResult":{"OgdDocumentResults":{"OgdDocumentReference":[]}}}',
            )
            fetcher._search_ogd(gsn="10001622", fassung_vom="2017-01-01")
            call_kwargs = mock_post.call_args
            assert call_kwargs is not None, "Must make a POST request"
            assert "FassungVom" in str(call_kwargs), (
                "POST body must contain FassungVom parameter"
            )

    def test_search_request_includes_gesetzesnummer(self):
        """Search request must include Gesetzesnummer parameter."""
        fetcher = OGDContentFetcher()
        with mock.patch.object(fetcher, "_http_post") as mock_post:
            mock_post.return_value = mock.Mock(
                status_code=200,
                text='{"OgdSearchResult":{"OgdDocumentResults":{"OgdDocumentReference":[]}}}',
            )
            fetcher._search_ogd(gsn="10001622", fassung_vom="2017-01-01")
            call_kwargs = mock_post.call_args
            assert "Gesetzesnummer" in str(call_kwargs), (
                "POST body must contain Gesetzesnummer"
            )
            assert "10001622" in str(call_kwargs), "Must include the correct GSN"

    def test_search_request_has_correct_url(self):
        """Search must POST to the v2.6 Bundesrecht endpoint."""
        fetcher = OGDContentFetcher()
        with mock.patch.object(fetcher, "_http_post") as mock_post:
            mock_post.return_value = mock.Mock(
                status_code=200,
                text='{"OgdSearchResult":{"OgdDocumentResults":{"OgdDocumentReference":[]}}}',
            )
            fetcher._search_ogd(gsn="10001622", fassung_vom="2017-01-01")
            called_url = mock_post.call_args[0][0]
            assert called_url == OGD_SEARCH_URL, (
                f"Must POST to {OGD_SEARCH_URL}"
            )


# ── Section type detection tests ────────────────────────────────────────────


class TestSectionTypeDetection:
    """Tests for detecting section type (Paragraf/Artikel/Anlage) from headings."""

    def test_detects_paragraph_from_heading(self):
        """§ heading must be classified as Paragraf."""
        fetcher = OGDContentFetcher()
        assert fetcher._detect_section_type("§ 1") == "Paragraf", (
            "§ heading must be Paragraf"
        )
        assert fetcher._detect_section_type("§ 100") == "Paragraf", (
            "Multi-digit § heading must be Paragraf"
        )

    def test_detects_article_from_heading(self):
        """Art. heading must be classified as Artikel."""
        fetcher = OGDContentFetcher()
        assert fetcher._detect_section_type("Art. 1") == "Artikel", (
            "Art. heading must be Artikel"
        )
        assert fetcher._detect_section_type("Art. 10") == "Artikel", (
            "Multi-digit Art. heading must be Artikel"
        )

    def test_detects_anlage_from_heading(self):
        """Anlage heading must be classified as Anlage."""
        fetcher = OGDContentFetcher()
        assert fetcher._detect_section_type("Anlage 1") == "Anlage", (
            "Anlage heading must be Anlage"
        )
        assert fetcher._detect_section_type("Anlage A") == "Anlage", (
            "Letter Anlage heading must be Anlage"
        )

    def test_unknown_heading_defaults_to_paragraph(self):
        """Unrecognized heading format must default to Paragraf."""
        fetcher = OGDContentFetcher()
        assert fetcher._detect_section_type("Something Else") == "Paragraf", (
            "Unknown heading must default to Paragraf"
        )

    def test_section_type_from_gldsym_in_xml(self, sample_dokument_xml_with_article):
        """Section type must be inferred from the gldsym element in the XML."""
        fetcher = OGDContentFetcher()
        result = fetcher._parse_dokument_xml(sample_dokument_xml_with_article)
        assert result.section_type == "Artikel", (
            "Art. 2 gldsym must be detected as Artikel"
        )

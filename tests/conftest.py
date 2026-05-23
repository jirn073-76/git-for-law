"""Shared pytest fixtures for git-for-law-austria tests."""

import pytest


ABGB_GSN = "10001622"
ABGB_ABBREV = "ABGB"
OGD_API_URL = "https://data.bka.gv.at/ris/api/v2.6/Bundesrecht"
CDX_API_URL = "https://web.archive.org/cdx/search/cdx"


# ── Sample OGD API v2.6 response data (real wrapper structure) ───────────────────
#
# Real OGD API v2.6 returns:
# { "OgdSearchResult": { "OgdDocumentResults": { "OgdDocumentReference": [...] } } }
#
# Fields use PascalCase: Inkrafttretensdatum, Aenderung, GesamteRechtsvorschriftUrl


@pytest.fixture
def sample_ogd_page_1():
    """First page of OGD API v2.6 response with 3 OgdDocumentReference items.

    Wrapped in OgdSearchResult.OgdDocumentResults.OgdDocumentReference[].
    Uses Inkrafttretensdatum (real field name), not fassung_vom.
    """
    return {
        "OgdSearchResult": {
            "OgdDocumentResults": {
                "OgdDocumentReference": [
                    {
                        "Inkrafttretensdatum": "2017-01-01",
                        "Aenderung": "BGBl. I Nr. 43/2016",
                        "Kurzinformation": "Aenderung des Allgemeinen buergerlichen Gesetzbuches",
                        "GesamteRechtsvorschriftUrl": (
                            "https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
                            "Abfrage=Bundesnormen&Gesetzesnummer=10001622&FassungVom=2017-01-01"
                        ),
                        "ArtikelParagraphAnlage": [
                            {"Typ": "Paragraf", "Bezeichnung": "§ 1", "Id": "F0025"},
                            {"Typ": "Paragraf", "Bezeichnung": "§ 2", "Id": "F0026"},
                            {"Typ": "Paragraf", "Bezeichnung": "§ 3", "Id": "F0027"},
                        ],
                    },
                    {
                        "Inkrafttretensdatum": "2017-01-01",
                        "Aenderung": "BGBl. I Nr. 43/2016",
                        "GesamteRechtsvorschriftUrl": (
                            "https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
                            "Abfrage=Bundesnormen&Gesetzesnummer=10001622&FassungVom=2017-01-01"
                        ),
                        "ArtikelParagraphAnlage": [
                            {"Typ": "Paragraf", "Bezeichnung": "§ 15", "Id": "F0040"},
                            {"Typ": "Paragraf", "Bezeichnung": "§ 16", "Id": "F0041"},
                        ],
                    },
                    {
                        "Inkrafttretensdatum": "2017-07-01",
                        "Aenderung": "BGBl. I Nr. 59/2017",
                        "GesamteRechtsvorschriftUrl": (
                            "https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
                            "Abfrage=Bundesnormen&Gesetzesnummer=10001622&FassungVom=2017-07-01"
                        ),
                        "ArtikelParagraphAnlage": [
                            {"Typ": "Paragraf", "Bezeichnung": "§ 1", "Id": "F0025"},
                            {"Typ": "Artikel", "Bezeichnung": "Art. 1", "Id": "A0001"},
                        ],
                    },
                ]
            },
            "GesamtzahlErgebnisse": 2556,
        }
    }


@pytest.fixture
def sample_ogd_single_item():
    """Single OgdDocumentReference for unit-testing parsing."""
    return {
        "Inkrafttretensdatum": "2018-01-01",
        "Aenderung": "BGBl. I Nr. 30/2018",
        "Kurzinformation": "Erbrechts-Aenderungsgesetz 2017",
        "GesamteRechtsvorschriftUrl": (
            "https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
            "Abfrage=Bundesnormen&Gesetzesnummer=10001622&FassungVom=2018-01-01"
        ),
        "ArtikelParagraphAnlage": [
            {"Typ": "Paragraf", "Bezeichnung": "§ 531", "Id": "F0531"},
        ],
    }


@pytest.fixture
def sample_ogd_duplicate_aenderung():
    """Two OgdDocumentReference items with same Aenderung but different Inkrafttretensdatum."""
    return [
        {
            "Inkrafttretensdatum": "2015-01-01",
            "Aenderung": "BGBl. I Nr. 87/2015",
            "GesamteRechtsvorschriftUrl": (
                "https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
                "Abfrage=Bundesnormen&Gesetzesnummer=10001622&FassungVom=2015-01-01"
            ),
            "ArtikelParagraphAnlage": [
                {"Typ": "Paragraf", "Bezeichnung": "§ 1", "Id": "F0025"},
            ],
        },
        {
            "Inkrafttretensdatum": "2015-06-01",
            "Aenderung": "BGBl. I Nr. 87/2015",
            "GesamteRechtsvorschriftUrl": (
                "https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
                "Abfrage=Bundesnormen&Gesetzesnummer=10001622&FassungVom=2015-06-01"
            ),
            "ArtikelParagraphAnlage": [
                {"Typ": "Paragraf", "Bezeichnung": "§ 1", "Id": "F0025"},
            ],
        },
    ]


@pytest.fixture
def sample_ogd_wrapper_response():
    """Full OgdSearchResult wrapper as returned by the real API.

    Used to test that the fetcher unwraps OgdSearchResult.OgdDocumentResults.OgdDocumentReference.
    """
    return {
        "OgdSearchResult": {
            "OgdDocumentResults": {
                "OgdDocumentReference": [
                    {
                        "Inkrafttretensdatum": "2020-01-01",
                        "Aenderung": "BGBl. I Nr. 100/2019",
                        "GesamteRechtsvorschriftUrl": (
                            "https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
                            "Abfrage=Bundesnormen&Gesetzesnummer=10001622&FassungVom=2020-01-01"
                        ),
                        "ArtikelParagraphAnlage": [
                            {"Typ": "Paragraf", "Bezeichnung": "§ 1", "Id": "F0025"},
                        ],
                    }
                ]
            },
            "GesamtzahlErgebnisse": 1,
        }
    }


@pytest.fixture
def sample_ogd_empty_response():
    """OGD API v2.6 response with no OgdDocumentReference items."""
    return {
        "OgdSearchResult": {
            "OgdDocumentResults": {
                "OgdDocumentReference": []
            },
            "GesamtzahlErgebnisse": 0,
        }
    }


@pytest.fixture
def sample_ogd_malformed_response():
    """Malformed OGD API response (not valid JSON)."""
    return b"<!DOCTYPE html><html><body>502 Bad Gateway</body></html>"


@pytest.fixture
def sample_ogd_all_versions():
    """Minimal set of ABGB versions as returned by OGD API v2.6 (abridged for tests)."""
    versions = []
    dates = [
        "1812-01-01", "1914-01-01", "1916-01-01", "1917-01-01",
        "1938-01-01", "1942-01-01", "1945-01-01", "1970-01-01",
        "1975-01-01", "2000-01-01", "2005-01-01", "2006-01-01",
        "2010-01-01", "2013-01-01", "2015-01-01", "2017-01-01",
        "2018-01-01", "2020-01-01", "2024-01-01", "2028-07-01",
    ]
    for date in dates:
        versions.append({
            "Inkrafttretensdatum": date,
            "Aenderung": f"Novelle {date}",
            "GesamteRechtsvorschriftUrl": (
                f"https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
                f"Abfrage=Bundesnormen&Gesetzesnummer=10001622&FassungVom={date}"
            ),
            "ArtikelParagraphAnlage": [
                {"Typ": "Paragraf", "Bezeichnung": "§ 1", "Id": "F0025"},
            ],
        })
    return versions


# ── Sample RIS HTML (real structure: generic elements, no Abs paragraphs) ────────
#
# Real RIS GeltendeFassung HTML uses generic <p>, <div>, <span> inside
# MainContent_*TextContainer_N divs. There is NO <p class="Abs"> in real pages.
# The parser must not rely on class="Abs" — it must capture ALL text elements.


@pytest.fixture
def sample_ris_html_simple():
    """RIS HTML with generic elements only — no Abs paragraphs."""
    return """
<html>
<body>
<div id="MainContent_ctl00_TextContainer_1">
    <h3 class="GldSymbol">&sect; 1</h3>
    <p>(1) Jeder Mensch hat von seiner Geburt an angeborene Rechte.</p>
    <p>(2) Niemand darf durch Vertrag oder Gesetz in diesen Rechten
    eingeschr&auml;nkt werden.</p>
</div>
<div id="MainContent_ctl00_TextContainer_2">
    <h3 class="GldSymbol">&sect; 2</h3>
    <p>Die allgemeinen Grunds&auml;tze des Rechts gelten f&uuml;r jedermann.</p>
    <div>Erg&auml;nzende Bestimmungen finden sich in den Nebengesetzen.</div>
</div>
</body>
</html>
"""


@pytest.fixture
def sample_ris_html_with_annotations():
    """RIS HTML with annotation markers that should be filtered out."""
    return """
<html>
<body>
<div id="MainContent_ctl00_TextContainer_1">
    <h3 class="GldSymbol">&sect; 1</h3>
    <p>Der allgemeine Teil regelt die Grundlagen.<sup>1)</sup></p>
    <p>Die Anmerkung enth&auml;lt Verweise auf die Gesetzesmaterialien.</p>
    <p class="Anmerkung">1) Vgl. BGBl. I Nr. 87/2015.</p>
    <p class="Fn">Fu&szlig;note: Siehe auch die Erl&auml;uterungen.</p>
    <p><a href="#fn1">1</a> Zur&uuml;ck zum Text.</p>
</div>
</body>
</html>
"""


@pytest.fixture
def sample_ris_html_malformed():
    """Malformed RIS HTML with unclosed tags and missing attributes."""
    return """
<html>
<body>
<div id="MainContent_ctl00_TextContainer_1">
    <h3 class="GldSymbol">&sect; 1
    <p>Text ohne schlie&szlig;endes p-Tag
    <div>Verschachteltes div ohne Ende
    <span>Weiterer Text</span>
</body>
</html>
"""


@pytest.fixture
def sample_ris_html_full_section():
    """Full RIS HTML for a single section with all expected content types (generic only)."""
    return """
<html>
<body>
<div id="MainContent_ctl00_TextContainer_5">
    <h3 class="GldSymbol">&sect; 16</h3>
    <h4 class="GldSymbol">I. Allgemeines</h4>
    <p>(1) Jeder Mensch hat angeborene, schon durch die Vernunft
    einleuchtende Rechte, und ist daher als eine Person zu betrachten.</p>
    <p>(2) Sklaven haben keine Rechte.</p>
    <p>Dieser Paragraph wurde mehrfach novelliert.</p>
    <span>Die &Uuml;bergangsbestimmungen finden sich in Art. 5.</span>
</div>
</body>
</html>
"""


@pytest.fixture
def sample_ris_html_multi_textcontainer():
    """RIS HTML with multiple TextContainer divs (different section types)."""
    return """
<html>
<body>
<div id="MainContent_ctl00_TextContainer_1">
    <h3 class="GldSymbol">&sect; 1</h3>
    <p>Paragraph eins Text.</p>
</div>
<div id="MainContent_ctl00_TextContainer_2">
    <h3 class="GldSymbol">Art. 1</h3>
    <p>Artikel eins Text.</p>
    <span>Zusatztext.</span>
</div>
<div id="MainContent_ctl00_TextContainer_3">
    <h3 class="GldSymbol">Anlage 1</h3>
    <p>Anhangtext.</p>
</div>
</body>
</html>
"""


@pytest.fixture
def sample_ris_html_no_generic_text():
    """RIS HTML TextContainer with absolutely no text content — empty container."""
    return """
<html>
<body>
<div id="MainContent_ctl00_TextContainer_1">
    <h3 class="GldSymbol">&sect; 99</h3>
</div>
</body>
</html>
"""


# ── Expected parser outputs ───────────────────────────────────────────────────

@pytest.fixture
def expected_parsed_section():
    """Expected JSON output for a parsed section from the RIS HTML."""
    return {
        "section_id": "§_1",
        "heading": "§ 1",
        "section_type": "Paragraf",
        "body": ("(1) Jeder Mensch hat von seiner Geburt an angeborene Rechte. "
                 "(2) Niemand darf durch Vertrag oder Gesetz in diesen Rechten "
                 "eingeschränkt werden."),
        "fassung_vom": "2017-01-01",
    }


@pytest.fixture
def expected_parsed_full_section():
    """Expected JSON output for § 16 from sample_ris_html_full_section."""
    return {
        "section_id": "§_16",
        "heading": "§ 16",
        "section_type": "Paragraf",
        "body": (
            "I. Allgemeines "
            "(1) Jeder Mensch hat angeborene, schon durch die Vernunft "
            "einleuchtende Rechte, und ist daher als eine Person zu betrachten. "
            "(2) Sklaven haben keine Rechte. "
            "Dieser Paragraph wurde mehrfach novelliert. "
            "Die Übergangsbestimmungen finden sich in Art. 5."
        ),
        "fassung_vom": "2017-01-01",
    }


@pytest.fixture
def expected_parsed_multi_section():
    """Expected JSON output for multiple sections parsed together."""
    return [
        {
            "section_id": "§_1",
            "heading": "§ 1",
            "section_type": "Paragraf",
            "body": "Paragraph eins Text.",
            "fassung_vom": "2020-01-01",
        },
        {
            "section_id": "Art_1",
            "heading": "Art. 1",
            "section_type": "Artikel",
            "body": "Artikel eins Text. Zusatztext.",
            "fassung_vom": "2020-01-01",
        },
        {
            "section_id": "Anlage_1",
            "heading": "Anlage 1",
            "section_type": "Anlage",
            "body": "Anhangtext.",
            "fassung_vom": "2020-01-01",
        },
    ]


# ── Wayback Machine fixtures ───────────────────────────────────────────────────
#
# CDX API does NOT index RIS query-parameter URLs. The real approach uses:
# 1. GesamteRechtsvorschriftUrl from OGD metadata to get the RIS URL
# 2. Construct Wayback URL directly: web.archive.org/web/{timestamp}id_/{ris_url}
# 3. Fetch with curl -L -A 'GitForLaw/1.0' to follow redirects


@pytest.fixture
def sample_wayback_html():
    """Sample HTML content as returned by Wayback Machine for a RIS page."""
    return """
<html>
<body>
<div id="MainContent_ctl00_TextContainer_1">
    <h3 class="GldSymbol">&sect; 1</h3>
    <p>(1) Jeder Mensch hat von seiner Geburt an angeborene Rechte.</p>
</div>
<div id="MainContent_ctl00_TextContainer_2">
    <h3 class="GldSymbol">&sect; 2</h3>
    <p>Die allgemeinen Grundsaetze des Rechts gelten fuer jedermann.</p>
</div>
</body>
</html>
"""


@pytest.fixture
def sample_wayback_empty_response():
    """Wayback Machine response for a page that was not archived."""
    return ""


@pytest.fixture
def sample_wayback_redirect_response():
    """Wayback Machine 302 redirect response (must use curl -L to follow)."""
    return {
        "status_code": 302,
        "headers": {"Location": "https://web.archive.org/web/20170101120000id_/https://..."},
        "body": "",
    }


@pytest.fixture
def sample_ris_url_from_ogd():
    """A real GesamteRechtsvorschriftUrl as returned by OGD API v2.6."""
    return (
        "https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
        "Abfrage=Bundesnormen&Gesetzesnummer=10001622&FassungVom=2017-01-01"
    )


@pytest.fixture
def sample_wayback_snapshot_url():
    """Expected Wayback Machine snapshot URL constructed from metadata."""
    return (
        "https://web.archive.org/web/20170101120000id_/"
        "https://www.ris.bka.gv.at/GeltendeFassung.wxe"
        "?Abfrage=Bundesnormen&Gesetzesnummer=10001622&FassungVom=2017-01-01"
    )


@pytest.fixture
def sample_cdx_response():
    """Sample Wayback CDX API response (one line per snapshot).

    CDX does NOT index RIS query-parameter URLs, so this fixture represents
    the general case where CDX is used for non-RIS URLs or as a fallback.
    """
    return [
        ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
        [
            "at,bka,ris)/geltendefassung/abgb",
            "20170101120000",
            "https://www.ris.bka.gv.at/GeltendeFassung/ABGB",
            "text/html",
            "200",
            "ABCD1234",
            "45678",
        ],
    ]


@pytest.fixture
def sample_cdx_empty_response():
    """Empty CDX response — the current reality for RIS query-parameter pages."""
    return [["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]]


@pytest.fixture
def sample_cdx_malformed_response():
    """Malformed CDX response (not valid JSON)."""
    return b"<html><body>Error 500</body></html>"


# ── Git and pipeline fixtures ─────────────────────────────────────────────────

@pytest.fixture
def sample_versions_by_date():
    """Grouped version data as the pipeline would produce."""
    return {
        "2017-01-01": [
            {"section_id": "§_1", "body": "Paragraph eins Text (2017)."},
            {"section_id": "§_2", "body": "Paragraph zwei Text (2017)."},
        ],
        "2018-01-01": [
            {"section_id": "§_1", "body": "Paragraph eins Text (2018 novelliert)."},
            {"section_id": "§_2", "body": "Paragraph zwei Text (2017)."},
        ],
        "2019-01-01": [
            {"section_id": "§_1", "body": "Paragraph eins Text (2019 novelliert)."},
        ],
    }


@pytest.fixture
def sample_empty_section():
    """A parsed section with empty body."""
    return {
        "section_id": "§_99",
        "heading": "§ 99",
        "section_type": "Paragraf",
        "body": "",
        "fassung_vom": "2020-01-01",
    }


@pytest.fixture
def sample_short_body_section():
    """A parsed section with body under 50 characters."""
    return {
        "section_id": "§_100",
        "heading": "§ 100",
        "section_type": "Paragraf",
        "body": "Kurzer Text.",
        "fassung_vom": "2020-01-01",
    }


@pytest.fixture
def sample_future_version():
    """Version data with a future fassung_vom date (2028-07-01)."""
    return {
        "fassung_vom": "2028-07-01",
        "sections": [
            {"section_id": "§_1", "body": "Paragraph eins (kuenftige Fassung)."},
        ],
        "aenderung": "BGBl. I Nr. 120/2027",
    }


@pytest.fixture
def sample_1022_section_version():
    """A version with 1022 sections (edge case: largest version)."""
    sections = []
    for i in range(1, 1023):
        sections.append({
            "section_id": f"§_{i}",
            "heading": f"§ {i}",
            "section_type": "Paragraf",
            "body": f"Text des Paragraphen {i}.",
            "fassung_vom": "1920-01-01",
        })
    return {
        "fassung_vom": "1920-01-01",
        "sections": sections,
        "aenderung": "Historische Fassung mit 1022 Paragrafen",
    }


# ── Harness fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def sample_harness_input_good():
    """Good quality repo — should score >= 85% overall."""
    return {
        "law_abbrev": "ABGB",
        "versions_expected": 3,
        "commits": [
            {"hash": "a1b2c3d4", "fassung_vom": "2017-01-01", "diff_lines": 45, "empty_diff": False},
            {"hash": "e5f6g7h8", "fassung_vom": "2018-01-01", "diff_lines": 52, "empty_diff": False},
            {"hash": "i9j0k1l2", "fassung_vom": "2019-01-01", "diff_lines": 38, "empty_diff": False},
        ],
        "duplicate_commits": 0,
        "sections": [
            {"section_id": "§_1", "body": "Langer Text mit mehr als 50 Zeichen Inhalt. " * 3},
            {"section_id": "§_2", "body": "Langer Text mit mehr als 50 Zeichen Inhalt. " * 3},
            {"section_id": "§_3", "body": "Kurz."},
        ],
    }


@pytest.fixture
def sample_harness_input_poor():
    """Poor quality repo — should score below 85% overall."""
    return {
        "law_abbrev": "ABGB",
        "versions_expected": 145,
        "commits": [
            {"hash": "a1b2c3d4", "fassung_vom": "2017-01-01", "diff_lines": 5, "empty_diff": False},
            {"hash": "a1b2c3d4", "fassung_vom": "2017-01-01", "diff_lines": 0, "empty_diff": True},
            {"hash": "e5f6g7h8", "fassung_vom": "2018-01-01", "diff_lines": 10, "empty_diff": False},
        ],
        "duplicate_commits": 1,
        "sections": [
            {"section_id": "§_1", "body": "Kurz."},
            {"section_id": "§_2", "body": ""},
            {"section_id": "§_3", "body": ""},
        ],
    }


# ── Diff fixtures (for test_diff.py) ──────────────────────────────────────────


@pytest.fixture
def sample_git_section_json_2017():
    """Section JSON content as stored in git repo for version 2017-01-01."""
    return {
        "section_id": "§_1",
        "heading": "§ 1",
        "body": "(1) Jeder Mensch hat angeborene Rechte. (2) Niemand darf eingeschraenkt werden.",
        "section_type": "Paragraf",
        "fassung_vom": "2017-01-01",
    }


@pytest.fixture
def sample_git_section_json_2018():
    """Section JSON content as stored in git repo for version 2018-01-01."""
    return {
        "section_id": "§_1",
        "heading": "§ 1",
        "body": "(1) Jeder Mensch hat angeborene, schon durch die Vernunft einleuchtende Rechte. "
               "(2) Niemand darf durch Vertrag in diesen Rechten eingeschraenkt werden.",
        "section_type": "Paragraf",
        "fassung_vom": "2018-01-01",
    }


@pytest.fixture
def sample_diff_repo_path(tmp_path):
    """Create a minimal git repo with two versions for diff testing.

    Returns the path to the law repo (tmp_path/laws/ABGB).
    """
    repo_path = tmp_path / "laws" / "ABGB"
    repo_path.mkdir(parents=True, exist_ok=True)
    return repo_path


@pytest.fixture
def sample_diff_ansi_output():
    """Expected ANSI-colored diff output patterns."""
    return {
        "addition_prefix": "\033[32m+",    # green for additions
        "deletion_prefix": "\033[31m-",    # red for deletions
        "header_prefix": "\033[1m",        # bold for headers
        "reset": "\033[0m",
    }


# ── Helper to construct expected commit messages ──────────────────────────────

@pytest.fixture
def expected_commit_message_format():
    """Fixture returning a lambda to build expected commit messages."""
    def _build(abbrev, fassung_vom, aenderung):
        aenderung_truncated = aenderung[:120] if len(aenderung) > 120 else aenderung
        return f"{abbrev} [{fassung_vom}]: {aenderung_truncated}"
    return _build

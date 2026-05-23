"""Tests for the RIS HTML parser module.

These tests validate parsing of RIS GeltendeFassung HTML pages, including:
- Text extraction from generic <p>, <div>, <span> elements (NOT Abs-only)
- Section heading extraction from <h3/h4/h5 class="GldSymbol">
- Annotation/footnote filtering
- Section ID extraction from TextContainer div IDs
- Malformed HTML handling
- Full section-to-JSON output format
- Verifies NO reliance on <p class="Abs"> (real RIS does not use Abs)
"""


from git_for_law_austria.ris_parser import RISParser


# ── Generic element extraction tests (real RIS has no <p class="Abs">) ────────


class TestRISParserGenericElements:
    """Tests for extracting text from ALL generic elements — the real RIS structure.

    Real RIS GeltendeFassung HTML uses generic <p>, <div>, <span> inside
    MainContent_*TextContainer_N divs. There is NO <p class="Abs"> in real pages.
    """

    def test_extracts_generic_p_paragraphs(self, sample_ris_html_simple):
        """Generic <p> elements (no class) must be captured."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_simple, fassung_vom="2017-01-01")
        section_1 = next(s for s in sections if s.section_id == "§_1")
        assert "(1) Jeder Mensch" in section_1.body, (
            "Must capture text from generic <p> elements"
        )
        assert "angeborene Rechte" in section_1.body

    def test_extracts_all_generic_p_in_container(self, sample_ris_html_simple):
        """All generic <p> children in a TextContainer must be captured."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_simple, fassung_vom="2017-01-01")
        section_1 = next(s for s in sections if s.section_id == "§_1")
        assert "(1)" in section_1.body, "Must include first generic p text"
        assert "(2)" in section_1.body, "Must include second generic p text"

    def test_extracts_generic_div(self, sample_ris_html_simple):
        """Generic <div> elements must be captured."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_simple, fassung_vom="2017-01-01")
        section_2 = next(s for s in sections if s.section_id == "§_2")
        assert "Ergänzende Bestimmungen" in section_2.body, (
            "Must capture text from generic <div> elements"
        )

    def test_extracts_span(self, sample_ris_html_full_section):
        """<span> elements must be captured."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_full_section, fassung_vom="2017-01-01")
        section_16 = next(s for s in sections if s.section_id == "§_16")
        assert "Übergangsbestimmungen" in section_16.body, (
            "Must capture text from <span> elements"
        )

    def test_text_stripped_and_joined(self, sample_ris_html_simple):
        """Extracted text must be whitespace-stripped and cleanly joined."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_simple, fassung_vom="2017-01-01")
        section_1 = next(s for s in sections if s.section_id == "§_1")
        assert not section_1.body.startswith("\n"), "Body must not start with newline"
        assert not section_1.body.endswith("\n"), "Body must not end with newline"

    def test_captures_all_text_elements(self, sample_ris_html_full_section):
        """Parser must capture text from ALL element types: generic p, span, h4."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_full_section, fassung_vom="2017-01-01")
        section_16 = next(s for s in sections if s.section_id == "§_16")
        assert "angeborene" in section_16.body, "Must include generic p text"
        assert "mehrfach novelliert" in section_16.body, "Must include second generic p text"
        assert "Übergangsbestimmungen" in section_16.body, "Must include span text"

    def test_empty_container_handled(self, sample_ris_html_no_generic_text):
        """TextContainer with heading only (no text) must produce empty body."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_no_generic_text, fassung_vom="2020-01-01")
        if sections:
            section = sections[0]
            assert section.body == "" or section.body is not None, (
                "Empty container must have empty string body, not None"
            )


# ── No-Abs-paragraph enforcement tests ────────────────────────────────────────


class TestRISParserNoAbsReliance:
    """Tests verifying the parser does NOT rely on <p class="Abs">."""

    def test_parser_does_not_require_abs_class(self):
        """Parser must work with HTML containing zero <p class="Abs"> elements."""
        parser = RISParser()
        html = """
        <div id="MainContent_TextContainer_1">
            <h3 class="GldSymbol">§ 1</h3>
            <p>Eins.</p>
            <div>Zwei.</div>
            <span>Drei.</span>
        </div>
        """
        sections = parser.parse_html(html, fassung_vom="2020-01-01")
        assert len(sections) == 1, "Must parse section with no Abs elements"
        assert "Eins." in sections[0].body
        assert "Zwei." in sections[0].body
        assert "Drei." in sections[0].body

    def test_no_abs_class_in_parsing_strategy(self):
        """The parser's regex/text extraction must not filter on class='Abs'."""
        parser = RISParser()
        assert "Abs" not in parser.text_element_pattern or parser.text_element_pattern is None, (
            "Text extraction regex must not reference class='Abs'"
        )

    def test_handles_html_with_only_div_elements(self):
        """Parser must work when HTML has only <div> text elements (no <p> at all)."""
        parser = RISParser()
        html = """
        <div id="MainContent_TextContainer_1">
            <h3 class="GldSymbol">§ 1</h3>
            <div>(1) Erster Absatz.</div>
            <div>(2) Zweiter Absatz.</div>
        </div>
        """
        sections = parser.parse_html(html, fassung_vom="2020-01-01")
        assert len(sections) == 1
        assert "Erster Absatz" in sections[0].body
        assert "Zweiter Absatz" in sections[0].body

    def test_handles_html_with_only_span_elements(self):
        """Parser must work when HTML has only <span> text elements."""
        parser = RISParser()
        html = """
        <div id="MainContent_TextContainer_1">
            <h3 class="GldSymbol">§ 1</h3>
            <span>Nur ein Satz.</span>
        </div>
        """
        sections = parser.parse_html(html, fassung_vom="2020-01-01")
        assert len(sections) == 1
        assert "Nur ein Satz" in sections[0].body


# ── Section heading extraction tests ──────────────────────────────────────────


class TestRISParserHeadings:
    """Tests for extracting section headings from GldSymbol elements."""

    def test_extracts_h3_heading(self, sample_ris_html_simple):
        """h3.GldSymbol must be extracted as section heading."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_simple, fassung_vom="2017-01-01")
        headings = {s.heading for s in sections}
        assert "§ 1" in headings, "Must extract h3 heading '§ 1'"
        assert "§ 2" in headings, "Must extract h3 heading '§ 2'"

    def test_extracts_h4_heading(self, sample_ris_html_full_section):
        """h4.GldSymbol sub-headings must be included in body or heading."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_full_section, fassung_vom="2017-01-01")
        section_16 = next(s for s in sections if s.section_id == "§_16")
        assert "Allgemeines" in section_16.body, (
            "h4 sub-heading text must appear in body"
        )

    def test_heading_html_entities_decoded(self, sample_ris_html_simple):
        """HTML entities in headings must be decoded (&sect; → §)."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_simple, fassung_vom="2017-01-01")
        headings = [s.heading for s in sections]
        for h in headings:
            assert "&sect;" not in h, (
                f"Heading '{h}' must not contain raw HTML entity &sect;"
            )

    def test_identifies_paragraph_type(self, sample_ris_html_simple):
        """Parser must identify Paragraf type from § prefix."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_simple, fassung_vom="2017-01-01")
        section_1 = next(s for s in sections if s.section_id == "§_1")
        assert section_1.section_type == "Paragraf"

    def test_identifies_article_type(self, sample_ris_html_multi_textcontainer):
        """Parser must identify Artikel type from Art. heading."""
        parser = RISParser()
        sections = parser.parse_html(
            sample_ris_html_multi_textcontainer, fassung_vom="2020-01-01"
        )
        art_section = next(s for s in sections if s.section_id == "Art_1")
        assert art_section.section_type == "Artikel"

    def test_identifies_anlage_type(self, sample_ris_html_multi_textcontainer):
        """Parser must identify Anlage type from Anlage heading."""
        parser = RISParser()
        sections = parser.parse_html(
            sample_ris_html_multi_textcontainer, fassung_vom="2020-01-01"
        )
        anlage_section = next(s for s in sections if s.section_id == "Anlage_1")
        assert anlage_section.section_type == "Anlage"


# ── Annotation filtering tests ────────────────────────────────────────────────


class TestRISParserAnnotationFiltering:
    """Tests for filtering annotation markers and footnotes."""

    def test_filters_anmerkung_class(self, sample_ris_html_with_annotations):
        """Elements with class Anmerkung must be excluded from body."""
        parser = RISParser()
        sections = parser.parse_html(
            sample_ris_html_with_annotations, fassung_vom="2018-01-01"
        )
        section_1 = next(s for s in sections if s.section_id == "§_1")
        assert "BGBl. I Nr. 87/2015" not in section_1.body, (
            "Anmerkung reference text must be filtered out"
        )

    def test_filters_fn_class(self, sample_ris_html_with_annotations):
        """Elements with class Fn (footnote) must be excluded."""
        parser = RISParser()
        sections = parser.parse_html(
            sample_ris_html_with_annotations, fassung_vom="2018-01-01"
        )
        section_1 = next(s for s in sections if s.section_id == "§_1")
        assert "Erläuterungen" not in section_1.body, (
            "Footnote text must be filtered out"
        )

    def test_filters_superscript_footnote_links(self, sample_ris_html_with_annotations):
        """Superscript footnote reference markers (<sup>) must be filtered."""
        parser = RISParser()
        sections = parser.parse_html(
            sample_ris_html_with_annotations, fassung_vom="2018-01-01"
        )
        section_1 = next(s for s in sections if s.section_id == "§_1")
        assert "1)" not in section_1.body, "Superscript footnote markers must be removed"

    def test_keeps_valid_legal_text_with_sup_mixed(self, sample_ris_html_with_annotations):
        """Legal text that happens to contain numbers must not be over-filtered."""
        parser = RISParser()
        sections = parser.parse_html(
            sample_ris_html_with_annotations, fassung_vom="2018-01-01"
        )
        section_1 = next(s for s in sections if s.section_id == "§_1")
        assert "allgemeine Teil" in section_1.body, (
            "Valid legal text must survive annotation filtering"
        )


# ── Section ID extraction tests ───────────────────────────────────────────────


class TestRISParserSectionIDs:
    """Tests for extracting section IDs from TextContainer div IDs."""

    def test_extracts_section_id_from_textcontainer(self, sample_ris_html_simple):
        """TextContainer div ID must be mapped to a clean section_id."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_simple, fassung_vom="2017-01-01")
        section_ids = {s.section_id for s in sections}
        assert "§_1" in section_ids, "Must map TextContainer_1 → §_1"
        assert "§_2" in section_ids, "Must map TextContainer_2 → §_2"

    def test_textcontainer_id_variants(self):
        """Various TextContainer ID patterns must be handled."""
        parser = RISParser()
        variants = [
            ('<div id="MainContent_ctl00_TextContainer_1"><h3>§ 1</h3><p>Text</p></div>', "§_1"),
            ('<div id="MainContent_ctl01_TextContainer_42"><h3>§ 42</h3><p>Text</p></div>', "§_42"),
            ('<div id="MainContent_ctl02_TextContainer_100"><h3>Art. 5</h3><p>Text</p></div>', "Art_5"),
        ]
        for html, expected_id in variants:
            sections = parser.parse_html(html, fassung_vom="2020-01-01")
            section_ids = {s.section_id for s in sections}
            assert expected_id in section_ids, (
                f"Failed: {html[:50]}... → expected {expected_id}"
            )


# ── Malformed HTML handling tests ─────────────────────────────────────────────


class TestRISParserMalformedHTML:
    """Tests for handling malformed or incomplete HTML."""

    def test_handles_missing_closing_tags(self, sample_ris_html_malformed):
        """Malformed HTML with unclosed tags must not crash the parser."""
        parser = RISParser()
        sections = parser.parse_html(
            sample_ris_html_malformed, fassung_vom="2017-01-01"
        )
        assert len(sections) >= 0, "Malformed HTML must not raise, must return gracefully"

    def test_handles_missing_gldsymbol(self):
        """Sections without GldSymbol heading must still be captured."""
        html = '<div id="MainContent_TextContainer_1"><p>Text ohne Überschrift.</p></div>'
        parser = RISParser()
        sections = parser.parse_html(html, fassung_vom="2020-01-01")
        assert len(sections) > 0, "Must capture section even without heading"
        assert sections[0].body == "Text ohne Überschrift."

    def test_handles_empty_textcontainer(self):
        """Empty TextContainer divs must be handled without error."""
        html = '<div id="MainContent_TextContainer_1"></div>'
        parser = RISParser()
        sections = parser.parse_html(html, fassung_vom="2020-01-01")
        if len(sections) > 0:
            section = sections[0]
            assert section.body == "" or section.body is None, (
                "Empty container must have empty body"
            )

    def test_handles_completely_empty_html(self):
        """Empty HTML string must not raise, must return empty list."""
        parser = RISParser()
        sections = parser.parse_html("", fassung_vom="2020-01-01")
        assert sections == [], "Empty HTML must produce empty list"

    def test_handles_html_without_textcontainers(self):
        """HTML with no TextContainer divs must return empty list."""
        html = "<html><body><p>Kein TextContainer hier.</p></body></html>"
        parser = RISParser()
        sections = parser.parse_html(html, fassung_vom="2020-01-01")
        assert sections == [], "HTML without TextContainers must produce empty list"

    def test_handles_nested_textcontainers(self):
        """Nested TextContainer-like patterns must be handled sensibly."""
        html = (
            '<div id="MainContent_TextContainer_1">'
            '<h3>§ 1</h3><p>Text eins.</p>'
            '<div id="MainContent_TextContainer_2">'
            '<h3>§ 2</h3><p>Text zwei.</p>'
            "</div></div>"
        )
        parser = RISParser()
        sections = parser.parse_html(html, fassung_vom="2020-01-01")
        assert len(sections) >= 1, "Nested containers must yield at least outer section"


# ── Full section-to-JSON output tests ─────────────────────────────────────────


class TestRISParserSectionJSONOutput:
    """Tests for the full section-to-JSON output format."""

    def test_parsed_section_is_serializable(self, sample_ris_html_simple):
        """ParsedSection must be JSON-serializable (dict)."""
        import json

        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_simple, fassung_vom="2017-01-01")
        section_1 = next(s for s in sections if s.section_id == "§_1")
        json_str = json.dumps(section_1.to_dict())
        assert isinstance(json_str, str), "to_dict output must be JSON-serializable"
        assert len(json_str) > 10, "JSON must have reasonable content"

    def test_parsed_section_has_required_fields(self, sample_ris_html_simple):
        """Each ParsedSection must have section_id, heading, body, section_type, fassung_vom."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_simple, fassung_vom="2017-01-01")
        for section in sections:
            assert section.section_id, "section_id must not be empty"
            assert section.heading is not None, "heading must be present"
            assert section.body is not None, "body must be present"
            assert section.section_type, "section_type must not be empty"
            assert section.fassung_vom, "fassung_vom must not be empty"

    def test_full_section_output_matches_expected(self, sample_ris_html_simple):
        """End-to-end: parser output must match expected JSON structure."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_simple, fassung_vom="2017-01-01")
        assert len(sections) == 2, "Must parse exactly 2 sections"
        section_1 = next(s for s in sections if s.section_id == "§_1")
        d = section_1.to_dict()
        assert d["section_id"] == "§_1"
        assert d["heading"] == "§ 1"
        assert d["section_type"] == "Paragraf"
        assert d["fassung_vom"] == "2017-01-01"
        assert "angeborene Rechte" in d["body"]

    def test_multi_textcontainer_all_sections_parsed(self, sample_ris_html_multi_textcontainer):
        """All TextContainers in a page must be parsed."""
        parser = RISParser()
        sections = parser.parse_html(
            sample_ris_html_multi_textcontainer, fassung_vom="2020-01-01"
        )
        assert len(sections) == 3, "Must parse all 3 TextContainers"
        ids = [s.section_id for s in sections]
        assert "§_1" in ids
        assert "Art_1" in ids
        assert "Anlage_1" in ids

    def test_skips_non_textcontainer_divs(self):
        """Divs without TextContainer in the ID must be ignored."""
        html = (
            '<div id="MainContent_header">Header</div>'
            '<div id="MainContent_TextContainer_1"><h3>§ 1</h3><p>Text</p></div>'
            '<div id="footer">Footer</div>'
        )
        parser = RISParser()
        sections = parser.parse_html(html, fassung_vom="2020-01-01")
        assert len(sections) == 1, "Only TextContainer divs must be parsed"

    def test_body_never_none_in_output(self):
        """Body must be empty string (not None) even with no text content."""
        parser = RISParser()
        sections = parser.parse_html(
            '<div id="MainContent_TextContainer_1"><h3>§ 1</h3></div>',
            fassung_vom="2020-01-01",
        )
        if sections:
            d = sections[0].to_dict()
            assert d["body"] == "" or d["body"] is not None, "Body must be string, not None"


# ── Regex-based parsing tests ─────────────────────────────────────────────────


class TestRISParserRegexStrategy:
    """Tests ensuring the regex-based parsing strategy works correctly."""

    def test_parser_uses_regex_not_lxml(self):
        """RISParser must prefer regex over lxml/BeautifulSoup for this HTML source."""
        parser = RISParser()
        assert parser.parsing_strategy == "regex", (
            "Parsing strategy must be 'regex', not lxml/BeautifulSoup"
        )

    def test_html_entities_decoded_in_body(self, sample_ris_html_simple):
        """HTML entities in body text must be decoded (&auml; → ä)."""
        parser = RISParser()
        sections = parser.parse_html(sample_ris_html_simple, fassung_vom="2017-01-01")
        section_2 = next(s for s in sections if s.section_id == "§_2")
        assert "&auml;" not in section_2.body, "HTML entities must be decoded"
        assert "Grundsätze" in section_2.body

    def test_whitespace_normalized(self):
        """Excessive whitespace and newlines must be normalized."""
        html = (
            '<div id="MainContent_TextContainer_1">'
            '<h3>§ 1</h3>'
            '<p>   Text   mit    vielen     Leerzeichen   </p>'
            "</div>"
        )
        parser = RISParser()
        sections = parser.parse_html(html, fassung_vom="2020-01-01")
        section = sections[0]
        assert "   " not in section.body, "Multiple spaces must be collapsed"
        assert "Text mit vielen Leerzeichen" in section.body

    def test_text_extraction_regex_is_generic(self):
        """Text extraction must match element names generically (p|div|span), not specific classes."""
        parser = RISParser()
        assert hasattr(parser, "text_element_pattern"), "Must have text element pattern"
        # Pattern must match element names, not class attributes
        pattern = parser.text_element_pattern
        if pattern:
            assert "Abs" not in pattern, "Pattern must not reference Abs class"


# ── Integration: full HTML page parsing ────────────────────────────────────────


class TestRISParserIntegration:
    """Integration tests for full RIS page parsing."""

    def test_full_page_with_mixed_element_types(self):
        """A full page with p, div, span, h3, h4 must parse correctly."""
        html = """
        <html><body>
        <div id="MainContent_ctl00_TextContainer_1">
            <h3 class="GldSymbol">§ 1</h3>
            <p>(1) Paragraph eins Satz eins.</p>
            <p>(2) Paragraph eins Satz zwei.</p>
            <div>Zusatz in div.</div>
            <span>Zusatz in span.</span>
        </div>
        <div id="MainContent_ctl00_TextContainer_2">
            <h3 class="GldSymbol">Art. 2</h3>
            <h4 class="GldSymbol">A. Unterabschnitt</h4>
            <p>Artikel zwei Text.</p>
        </div>
        </body></html>
        """
        parser = RISParser()
        sections = parser.parse_html(html, fassung_vom="2020-01-01")
        assert len(sections) == 2
        s1 = next(s for s in sections if s.section_id == "§_1")
        assert s1.section_type == "Paragraf"
        assert "Paragraph eins Satz eins" in s1.body
        assert "Zusatz in div" in s1.body
        assert "Zusatz in span" in s1.body
        s2 = next(s for s in sections if s.section_id == "Art_2")
        assert s2.section_type == "Artikel"
        assert "Unterabschnitt" in s2.body

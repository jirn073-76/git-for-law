"""Regex-based RIS GeltendeFassung HTML parser."""

import html as html_mod
import re
from dataclasses import dataclass


@dataclass
class ParsedSection:
    """A parsed legal section from RIS HTML."""

    section_id: str = ""
    heading: str = ""
    section_type: str = ""
    body: str = ""
    fassung_vom: str = ""

    def to_dict(self) -> dict:
        return {
            "section_id": self.section_id,
            "heading": self.heading,
            "section_type": self.section_type,
            "body": self.body,
            "fassung_vom": self.fassung_vom,
        }


class RISParser:
    """Regex-based parser for RIS GeltendeFassung HTML.

    Parses GldSymbol headings and Abs text within
    MainContent_*TextContainer_N divs.
    """

    parsing_strategy = "regex"
    text_element_pattern = r"<(p|div|span)[^>]*>(.*?)</\1>"

    def __init__(self):
        self._current_article = ""

    def parse_html(self, html_text: str, fassung_vom: str) -> list:
        if not html_text:
            return []

        container_pattern = re.compile(
            r'<div[^>]*id="MainContent_[^"]*TextContainer_(\d+)"[^>]*>',
            re.DOTALL | re.IGNORECASE,
        )

        matches = list(container_pattern.finditer(html_text))
        if not matches:
            return []

        sections = []
        section_counter = 0
        seen_ids = {}
        self._current_article = ""
        for i, match in enumerate(matches):
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(html_text)
            container_content = html_text[start:end]

            self._update_article_context(container_content)
            container_sections = self._parse_container(container_content, fassung_vom)
            for section in container_sections:
                if section.heading:
                    section.section_id = self._derive_section_id(section.heading)
                    section.section_type = self._derive_section_type(section.heading)
                if not section.section_id:
                    if not section.body or not section.body.strip():
                        continue
                    section_counter += 1
                    section.section_id = f"Section-{section_counter}"
                    section.section_type = "Paragraf"
                if section.section_id in seen_ids:
                    seen_ids[section.section_id] += 1
                    section.section_id = f"{section.section_id}_{seen_ids[section.section_id]}"
                else:
                    seen_ids[section.section_id] = 1
                sections.append(section)

        sections.sort(key=lambda s: self._section_sort_key(s.section_id))
        return sections

    def _update_article_context(self, container_html: str) -> None:
        """Extract article context from UeberschrG1 headings to track across containers."""
        for cls in ("UeberschrG1", "UeberschrG2"):
            pattern = re.compile(
                rf'<h4\s+class="{cls}[^"]*"[^>]*>(.*?)</h4>',
                re.DOTALL | re.IGNORECASE,
            )
            for m in pattern.finditer(container_html):
                text = self._clean_group_heading(m.group(1))
                if not text or text.startswith("(Anm.") or text.startswith("(Anm:"):
                    continue
                art_m = re.search(
                    r'Art(?:ikel)?\.?\s*([IVXLCDM]+[a-z]?|\d+[a-z]?)\b',
                    text, re.IGNORECASE,
                )
                if art_m and self._is_valid_roman_or_num(art_m.group(1)):
                    self._current_article = f"Artikel {art_m.group(1)}"

    def _is_section_heading(self, heading: str) -> bool:
        """Check if a heading is a real section heading (not a sub-heading or annotation)."""
        if heading.startswith("(Anm.") or heading.startswith("(Anm:"):
            return False
        h = heading.lower()
        return h.startswith("§") or h.startswith("art") or h.startswith("anlage")

    def _parse_container(self, container_html: str, fassung_vom: str) -> list:
        """Parse a container. May contain multiple sections (e.g. ParagraphMitAbsatzzahl)."""
        gld_pattern = re.compile(
            r'<h[345]\s+class="(?:GldSymbol|UeberschrPara|UeberschrArt|Anlagenbez)[^"]*"\s*>(.*?)</h[345]>',
            re.DOTALL | re.IGNORECASE,
        )
        gld_matches = list(gld_pattern.finditer(container_html))

        # Pre-filter annotation headings so containers with only annotations
        # fall through to the standalone-article path.
        valid_gld = []
        for gm in gld_matches:
            h = self._clean_heading(gm.group(1))
            if h and not h.startswith("(Anm.") and not h.startswith("(Anm:"):
                valid_gld.append(gm)

        if not valid_gld:
            return self._parse_container_standalone(container_html, fassung_vom)

        sections = []
        pending_prefix = ""
        group_heading_applied = False

        for i, gld_match in enumerate(valid_gld):
            heading = self._clean_heading(gld_match.group(1))

            body_start = gld_match.end()
            body_end = valid_gld[i + 1].start() if i + 1 < len(valid_gld) else len(container_html)
            body_html = container_html[body_start:body_end]
            body = self._extract_body(body_html)

            if self._is_section_heading(heading):
                if pending_prefix:
                    heading = f"{pending_prefix} — {heading}"
                    pending_prefix = ""
                if not group_heading_applied:
                    group_heading_applied = True
                    pre_html = container_html[: gld_match.start()]
                    group_heading = self._extract_group_heading(pre_html)
                    heading_is_par = heading.startswith("§")
                    if group_heading:
                        heading = f"{group_heading} — {heading}"
                        if self._current_article and heading_is_par:
                            group_has_art = re.search(
                                r'Art(?:ikel)?\.?\s*[IVXLCDM\d]', group_heading, re.IGNORECASE
                            )
                            if not group_has_art:
                                heading = f"{self._current_article} — {heading}"
                    elif self._current_article and heading_is_par:
                        heading = f"{self._current_article} — {heading}"

                art_m = re.match(
                    r'Art(?:ikel)?\.?\s*([IVXLCDM]+[a-z]?|\d+[a-z]?)\b',
                    heading, re.IGNORECASE,
                )
                if art_m and self._is_valid_roman_or_num(art_m.group(1)):
                    self._current_article = f"Artikel {art_m.group(1)}"
                sections.append(ParsedSection(
                    heading=heading, body=body, fassung_vom=fassung_vom,
                ))
            else:
                # Sub-heading — defer as prefix if no section yet, else attach to last section
                if sections:
                    sub_text = heading
                    if body:
                        sub_text = f"{sub_text} {body}"
                    prev = sections[-1]
                    prev.body = f"{prev.body}\n{sub_text}".strip()
                else:
                    pending_prefix = f"{pending_prefix} — {heading}" if pending_prefix else heading

        if pending_prefix and not sections:
            body = self._extract_body(container_html)
            section_id = self._extract_section_from_body(body) if body else ""
            if section_id:
                heading = f"{pending_prefix} — {section_id.replace('§_', '§ ').replace('Art_', 'Art. ').replace('Anlage_', 'Anlage ')}"
            else:
                heading = pending_prefix
            sections.append(ParsedSection(
                heading=heading, body=body, fassung_vom=fassung_vom,
            ))

        return sections

    def _parse_container_standalone(self, container_html: str, fassung_vom: str) -> list:
        """Handle containers with no GldSymbol/UeberschrPara headings.

        Catches standalone articles (UeberschrG1 heading + body text, no
        § subsections) and unlabeled text sections.
        """
        heading = self._extract_heading_legacy(container_html)
        body = self._extract_body(container_html)

        # Try heading first (e.g. UeberschrG1 "Artikel X") before scanning
        # body text for cross-references.
        section_id = ""
        if heading:
            section_id = self._derive_section_id(heading)
        if not section_id and body:
            section_id, erltext_title = self._extract_section_from_erltext(container_html)
        if not section_id and body:
            section_id = self._extract_section_from_body(body)

        if section_id:
            if heading and heading != "Text" and not self._is_section_heading(heading):
                heading = f"{heading} — {section_id.replace('§_', '§ ').replace('Art_', 'Art. ').replace('Anlage_', 'Anlage ')}"
            elif not heading or heading == "Text":
                heading = section_id.replace("§_", "§ ").replace("Art_", "Art. ").replace("Anlage_", "Anlage ")
        elif heading == "Text":
            heading = ""

        if heading or body:
            return [ParsedSection(
                heading=heading, body=body, fassung_vom=fassung_vom,
            )]
        return []

    def _extract_section_from_erltext(self, container_html: str) -> tuple:
        """Extract (section_id, full_title) from p.ErlText elements.

        Returns ("", "") if no valid §/Art/Anlage marker found, or if the
        ErlText is an annotation like '(Anm.: ...)'.
        """
        erltext_pattern = re.compile(
            r'<p\s+class="[^"]*ErlText[^"]*"[^>]*>(.*?)</p>',
            re.DOTALL | re.IGNORECASE,
        )
        for m in erltext_pattern.finditer(container_html):
            text = self._clean_text(m.group(1))
            # Skip annotation paragraphs like "(Anm.: ...)"
            if re.match(r'\(Anm\.?:', text):
                continue
            sid = self._extract_section_id_from_text(text)
            if sid:
                # Normalise the title for display: "§ 1. Erwerbsvorgänge." or "Artikel I."
                display = sid.replace("§_", "§ ").replace("Art_", "Art. ").replace("Anlage_", "Anlage ")
                # If the ErlText has a title after the number, use it
                if text != display and text != display + ".":
                    display = text
                return (sid, display)
        return ("", "")

    def _extract_section_id_from_text(self, text: str) -> str:
        """Extract §_N / Art_N / Anlage_N from plain text (no HTML)."""
        # Check Artikel first for compound patterns like "Art. I § 1"
        m = re.search(r'Art(?:ikel)?\.?\s*([IVXLCDM]+[a-z]?|\d+[a-z]?)\.?', text, re.IGNORECASE)
        if m and self._is_valid_roman_or_num(m.group(1)):
            art_num = m.group(1)
            par_m = re.search(r'§\.?\s*(\d+[a-z]?)\.', text, re.IGNORECASE)
            if par_m:
                return f'Art_{art_num}_§_{par_m.group(1)}'
            return f'Art_{m.group(1)}'
        m = re.search(r'§\.?\s*(\d+[a-z]?)\.', text, re.IGNORECASE)
        if m:
            return f'§_{m.group(1)}'
        m = re.search(r'Anlage\.?\s*([IVXLCDM]+[a-z]?|\d+[a-z]?)\.?', text, re.IGNORECASE)
        if m and self._is_valid_roman_or_num(m.group(1)):
            return f'Anlage_{m.group(1)}'
        return ""

    def _extract_section_from_body(self, body_text: str) -> str:
        """Extract section ID from inline §/Art/Anlage markers in body text."""
        if not body_text:
            return ""
        return self._extract_section_id_from_text(body_text)

    def _extract_heading_legacy(self, container_html: str) -> str:
        """Extract heading from h3/h4/h5 elements without GldSymbol class."""
        for level in ("h3", "h4", "h5"):
            for m in re.finditer(
                rf'<{level}([^>]*)>(.*?)</{level}>',
                container_html,
                re.DOTALL | re.IGNORECASE,
            ):
                attrs = m.group(1)
                if 'onlyScreenreader' in attrs:
                    continue
                text = self._clean_heading(m.group(2))
                if text and text != "Text" and text != "Beachte für folgende Bestimmung":
                    return text
        return ""

    def _clean_group_heading(self, heading_html: str) -> str:
        """Clean a UeberschrG1/G2 heading, replacing <br> with space."""
        text = self._strip_screen_reader(heading_html)
        text = re.sub(r'<br\s*/?>', ' ', text, flags=re.IGNORECASE)
        text = self._strip_tags(text)
        text = self._decode_entities(text)
        text = text.replace('\xa0', ' ')
        return self._normalize_whitespace(text)

    def _extract_group_heading(self, html: str) -> str:
        """Extract UeberschrG1 or UeberschrPara heading, skipping annotations."""
        for cls in ("UeberschrG1", "UeberschrG2", "UeberschrPara"):
            pattern = re.compile(
                rf'<h4\s+class="{cls}[^"]*"[^>]*>(.*?)</h4>',
                re.DOTALL | re.IGNORECASE,
            )
            match = pattern.search(html)
            if match:
                text = self._clean_group_heading(match.group(1))
                if text and not text.startswith("(Anm.") and not text.startswith("(Anm:"):
                    return text
        return ""

    def _clean_heading(self, heading_html: str) -> str:
        """Extract clean heading from GldSymbol content, removing sr-only spans."""
        # Remove sr-only spans (screen reader duplicates)
        cleaned = re.sub(
            r'<span[^>]*class="[^"]*sr-only[^"]*"[^>]*>.*?</span>',
            '',
            heading_html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Replace <br> with space before stripping tags
        cleaned = re.sub(r'<br\s*/?>', ' ', cleaned, flags=re.IGNORECASE)
        # Extract remaining text (from aria-hidden spans or direct)
        text = self._strip_tags(cleaned)
        text = self._decode_entities(text)
        text = text.replace(" ", " ")  # Replace &nbsp; with space
        return self._normalize_whitespace(text)

    def _extract_body(self, body_html: str) -> str:
        """Extract legal text from body HTML, preserving paragraph structure."""
        positioned = []  # (position, text) tuples for document order

        # Identify wai-absatz-list regions for exclusion
        list_regions = []
        list_pattern = re.compile(
            r'<ol\s+class="[^"]*wai-absatz-list[^"]*"[^>]*>(.*?)</ol>',
            re.DOTALL | re.IGNORECASE,
        )
        for list_match in list_pattern.finditer(body_html):
            list_regions.append((list_match.start(), list_match.end()))
            list_content = list_match.group(1)
            # Content offset: position within body_html where list_content starts
            content_offset = list_match.start() + list_match.group(0).index('>') + 1
            for abs_match in re.finditer(
                r'<div\s+class="[^"]*Abs[^"]*"[^>]*>(.*?)</div>',
                list_content,
                re.DOTALL | re.IGNORECASE,
            ):
                text = self._clean_text(abs_match.group(1))
                if text:
                    positioned.append((content_offset + abs_match.start(), text))
            # p.Abs may also appear inside list items (e.g. standalone
            # article containers where RIS embeds numbered paragraphs
            # after an Abs div within the same li).
            for p_match in re.finditer(
                r'<p\s+class="[^"]*Abs[^"]*"[^>]*>(.*?)</p>',
                list_content,
                re.DOTALL | re.IGNORECASE,
            ):
                text = self._clean_text(p_match.group(1))
                if text:
                    positioned.append((content_offset + p_match.start(), text))
            for tag in ("Aufzaehlung", "Schlussteil"):
                for m in re.finditer(
                    rf'<div\s+class="[^"]*{tag}[^"]*"[^>]*>(.*?)</div>',
                    list_content,
                    re.DOTALL | re.IGNORECASE,
                ):
                    text = self._clean_text(m.group(1))
                    if text:
                        positioned.append((content_offset + m.start(), text))

        def _in_list(pos):
            for s, e in list_regions:
                if s <= pos < e:
                    return True
            return False

        for p_match in re.finditer(
            r'<p\s+class="[^"]*Abs[^"]*"[^>]*>(.*?)</p>',
            body_html,
            re.DOTALL | re.IGNORECASE,
        ):
            if not _in_list(p_match.start()):
                text = self._clean_text(p_match.group(1))
                if text:
                    positioned.append((p_match.start(), text))

        for div_match in re.finditer(
            r'<div\s+class="[^"]*Abs[^"]*"[^>]*>(.*?)</div>',
            body_html,
            re.DOTALL | re.IGNORECASE,
        ):
            if not _in_list(div_match.start()):
                text = self._clean_text(div_match.group(1))
                if text:
                    positioned.append((div_match.start(), text))

        for tag_pattern in (r'AufzaehlungE\d', r'SchlussteilE\d'):
            for m in re.finditer(
                rf'<div\s+class="[^"]*{tag_pattern}[^"]*"[^>]*>(.*?)</div>',
                body_html,
                re.DOTALL | re.IGNORECASE,
            ):
                if not _in_list(m.start()):
                    text = self._clean_text(m.group(1))
                    if text:
                        positioned.append((m.start(), text))

        if not positioned:
            p_texts = []
            for m in re.finditer(
                r"<p[^>]*>(.*?)</p>",
                body_html,
                re.DOTALL | re.IGNORECASE,
            ):
                tag_text = m.group(0)
                if self._is_annotation_element(tag_text):
                    continue
                open_tag = tag_text[: tag_text.find(">")]
                if 'sr-only' in open_tag:
                    continue
                if 'aria-hidden' in open_tag:
                    text = self._strip_tags(m.group(1))
                    text = self._decode_entities(text)
                    text = text.replace(" ", " ")
                    text = self._normalize_whitespace(text)
                else:
                    text = self._clean_text(m.group(1))
                if text:
                    p_texts.append(text)
            positioned.extend((i, t) for i, t in enumerate(p_texts))
            for tag in ("div", "span"):
                for m in re.finditer(
                    rf"<{tag}[^>]*>(.*?)</{tag}>",
                    body_html,
                    re.DOTALL | re.IGNORECASE,
                ):
                    tag_text = m.group(0)
                    if self._is_annotation_element(tag_text):
                        continue
                    open_tag = tag_text[: tag_text.find(">")]
                    if 'sr-only' in open_tag:
                        continue
                    if "aria-hidden" in tag_text:
                        text = self._strip_tags(m.group(1))
                        text = self._decode_entities(text)
                        text = text.replace(" ", " ")
                        text = self._normalize_whitespace(text)
                    else:
                        text = self._clean_text(m.group(1))
                    if text:
                        positioned.append((len(positioned), text))

        positioned.sort(key=lambda x: x[0])
        body = "\n".join(p[1] for p in positioned)
        return self._normalize_whitespace(body)

    def _clean_text(self, html_fragment: str) -> str:
        """Clean a text fragment: strip sr-only, aria-hidden spans, decode entities."""
        # Remove sr-only spans entirely
        cleaned = re.sub(
            r'<span[^>]*class="[^"]*sr-only[^"]*"[^>]*>.*?</span>',
            '',
            html_fragment,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Keep only the visible text from aria-hidden spans
        cleaned = re.sub(
            r'<span[^>]*aria-hidden="true"[^>]*>(.*?)</span>',
            r'\1',
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Remove superscript footnote markers entirely (both tag and content)
        cleaned = re.sub(
            r'<sup[^>]*>.*?</sup>',
            '',
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Strip remaining tags
        text = self._strip_tags(cleaned)
        text = self._decode_entities(text)
        text = text.replace(" ", " ")
        return self._normalize_whitespace(text)

    def _is_annotation_element(self, tag_html: str) -> bool:
        """Check if an HTML element is an annotation or footnote."""
        class_match = re.search(r'class="([^"]*)"', tag_html, re.IGNORECASE)
        if class_match:
            classes = class_match.group(1).lower().split()
            for cls in classes:
                if cls in ("anmerkung", "fn", "footnote"):
                    return True
        # Check for footnote link patterns
        inner = re.search(r'<a[^>]*href="[^"]*#[^"]*"[^>]*>.*?</a>', tag_html, re.IGNORECASE)
        if inner:
            return True
        return False

    @staticmethod
    def _strip_screen_reader(html_fragment: str) -> str:
        """Remove sr-only spans but keep visible text."""
        cleaned = re.sub(
            r'<span[^>]*class="[^"]*sr-only[^"]*"[^>]*>.*?</span>',
            '',
            html_fragment,
            flags=re.DOTALL | re.IGNORECASE,
        )
        return cleaned

    def _derive_section_id(self, heading: str) -> str:
        # Artikel must be checked FIRST to preserve article context in compound
        # headings like "Artikel I — § 1." where both article and paragraph appear.
        art_m = re.search(r'Art(?:ikel)?\.?\s*([IVXLCDM]+[a-z]?|\d+[a-z]?)\b\.?', heading, re.IGNORECASE)
        if art_m and self._is_valid_roman_or_num(art_m.group(1)):
            art_num = art_m.group(1)
            par_matches = re.findall(r'§\.?\s*(\d+[a-z]*\d*)\b\.?', heading, re.IGNORECASE)
            if par_matches:
                return f"Art_{art_num}_§_{par_matches[-1]}"
            return f"Art_{art_num}"
        m = re.search(r'§\.?\s*(\d+[a-z]*\d*)\b\.?', heading, re.IGNORECASE)
        if m:
            return f"§_{m.group(1)}"
        m = re.search(r'(?:Anlage|Anh(?:ang)?|ANHANG)\.?\s*([IVXLCDM]+[a-z]?|\d+[a-z]?|[A-Z])\b\.?', heading)
        if m and self._is_valid_roman_or_num_or_letter(m.group(1)):
            return f"Anlage_{m.group(1)}"
        if re.match(r'(?:Anlage|Anh(?:ang)?|ANHANG)\.?/?(\d+)', heading):
            m = re.match(r'(?:Anlage|Anh(?:ang)?|ANHANG)\.?/?(\d+)', heading)
            return f"Anlage_{m.group(1)}"
        if re.match(r'(?:Anlage|Anh(?:ang)?|ANHANG)\s*$', heading.strip()):
            return "Anlage_1"
        return ""

    def _derive_section_type(self, heading: str) -> str:
        if heading.startswith("§"):
            return "Paragraf"
        if re.search(r'Art(?:ikel)?[\s.]', heading):
            if re.search(r'§\.?\s*\d+', heading):
                return "Paragraf"
            return "Artikel"
        if re.match(r'(?:Anlage|Anh(?:ang)?|ANHANG)', heading):
            return "Anlage"
        return "Paragraf"

    @staticmethod
    def _roman_to_int(s: str):
        try:
            result = 0
            values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
            prev = 0
            for c in reversed(s.upper()):
                v = values[c]
                if v >= prev:
                    result += v
                else:
                    result -= v
                prev = v
            return result
        except (KeyError, ValueError):
            return None

    @staticmethod
    def _section_sort_key(section_id: str):
        """Natural sort: § first, then Art, then Anlage, then fallbacks.

        Compound keys like Art_I_§_1 sort by article number first, then
        paragraph number within the same article.
        """
        _PREFIX_ORDER = {"§": 0, "Art": 1, "Anlage": 2}
        m_compound = re.match(r"Art_([^_]+)_§_(\d+)([a-z]?)(?:_(\d+))?$", section_id)
        if m_compound:
            art_part = m_compound.group(1)
            par_num = int(m_compound.group(2))
            par_suffix = m_compound.group(3)
            dedup = int(m_compound.group(4) or 0)
            roman_val = RISParser._roman_to_int(art_part)
            if roman_val is not None:
                return (1, 1, roman_val, "", 0, par_num, par_suffix, dedup)
            art_m = re.match(r"(\d+)([a-z]?)$", art_part)
            if art_m:
                return (1, 0, int(art_m.group(1)), art_m.group(2), 0, par_num, par_suffix, dedup)
            return (1, 0, 0, art_part, 0, par_num, par_suffix, dedup)
        parts = section_id.split("_", 1)
        if len(parts) == 2:
            prefix, numpart = parts
            pf_rank = _PREFIX_ORDER.get(prefix, 3)
            m = re.match(r"(\d+)([a-z]?)(?:_(\d+))?$", numpart)
            if m:
                dedup = int(m.group(3) or 0)
                return (pf_rank, 0, int(m.group(1)), m.group(2), dedup)
            roman_val = RISParser._roman_to_int(numpart)
            if roman_val is not None:
                return (pf_rank, 1, roman_val, "")
            return (pf_rank, 0, 0, section_id)
        sm = re.match(r"Section-(\d+)", section_id)
        if sm:
            return (3, 0, int(sm.group(1)), "")
        return (4, 0, 0, section_id)

    @staticmethod
    def _is_valid_roman_or_num(s: str) -> bool:
        if re.match(r'^\d+[a-z]*\d*$', s):
            return True
        base = re.sub(r'[a-z]$', '', s)
        return bool(re.match(r'^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$', base.upper()))

    @staticmethod
    def _is_valid_roman_or_num_or_letter(s: str) -> bool:
        if re.match(r'^\d+[a-z]?$', s):
            return True
        if re.match(r'^[A-Z]$', s):
            return True
        return bool(re.match(r'^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$', s.upper()))

    @staticmethod
    def _decode_entities(text: str) -> str:
        return html_mod.unescape(text)

    @staticmethod
    def _strip_tags(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text)

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

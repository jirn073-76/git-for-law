"""OGD API v2.6 fetcher for Austrian federal law metadata."""

import json
import time
from dataclasses import dataclass, field

import requests


@dataclass
class OGDVersionItem:
    """A single Fassung (version) from the OGD API."""

    fassung_vom: str = ""
    aenderung: str = ""
    kundmachungsorgan: str = ""
    ris_url: str = ""
    sections: list = field(default_factory=list)


@dataclass
class OGDPaginatedResult:
    """Result of a paginated OGD API query."""

    items: list
    total_count: int
    page: int
    per_page: int = 10

    @property
    def has_more(self) -> bool:
        """Whether more pages are available."""
        if self.per_page <= 0:
            return False
        return self.page * self.per_page < self.total_count


class OGDFetcher:
    """Fetches version metadata from the Austrian OGD API v2.6."""

    API_URL = "https://data.bka.gv.at/ris/api/v2.6/Bundesrecht"
    ABGB_GSN = "10001622"

    def __init__(self, rate_limit: float = 0.3):
        self._last_request_time = 0.0
        self.min_interval = rate_limit

    @property
    def api_url(self) -> str:
        return self.API_URL

    def _build_request_body(
        self, gsn: str, page: int = 1, per_page: str = "Ten"
    ) -> str:
        return (
            f"Applikation=BrKons"
            f"&Gesetzesnummer={gsn}"
            f"&Seitennummer={page}"
            f"&DokumenteProSeite={per_page}"
        )

    def _build_headers(self) -> dict:
        return {"Content-Type": "application/x-www-form-urlencoded"}

    def _rate_limited_request(self, method: str, url: str, data: str = "") -> requests.Response:
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        while time.monotonic() - self._last_request_time < self.min_interval:
            time.sleep(0.01)
        headers = self._build_headers()
        response = requests.request(method, url, data=data, headers=headers)
        self._last_request_time = time.monotonic()
        return response

    def _paginate(self, gsn: str, per_page: str = "OneHundred", max_pages: int = 3):
        page = 1
        while page <= max_pages:
            body = self._build_request_body(gsn=gsn, page=page, per_page=per_page)
            yield page, body
            page += 1

    def _unwrap_references(self, response: dict) -> list:
        return response["OgdSearchResult"]["OgdDocumentResults"]["OgdDocumentReference"]

    def _extract_total_count(self, response: dict) -> int:
        try:
            hits = response["OgdSearchResult"]["OgdDocumentResults"]["Hits"]
            return int(hits.get("#text", 0))
        except (KeyError, TypeError, ValueError):
            return response["OgdSearchResult"].get("GesamtzahlErgebnisse", 0)

    def _handle_response(self, response) -> None:
        if response.status_code != 200:
            raise Exception(
                f"OGD API returned status {response.status_code}: {response.text}"
            )

    def _parse_response_text(self, text) -> dict:
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            raise Exception("Failed to parse OGD API JSON response")

    def _parse_response(self, response: dict) -> list:
        if "OgdSearchResult" not in response:
            raise Exception("Response missing OgdSearchResult wrapper")
        items = self._unwrap_references(response)
        return [self._parse_item(item) for item in items]

    def _extract_item_data(self, item_data: dict) -> dict:
        """Extract flat item data from either nested real API or flat test format."""
        if "Data" in item_data:
            brkons = (
                item_data.get("Data", {})
                .get("Metadaten", {})
                .get("Bundesrecht", {})
                .get("BrKons", {})
            )
            return {
                "Inkrafttretensdatum": brkons.get("Inkrafttretensdatum", ""),
                "Aenderung": brkons.get("Aenderung", ""),
                "Kundmachungsorgan": brkons.get("Kundmachungsorgan", ""),
                "ArtikelParagraphAnlage": brkons.get("ArtikelParagraphAnlage", ""),
                "Abkuerzung": brkons.get("Abkuerzung", ""),
            }
        return item_data

    def _parse_item(self, item_data: dict) -> OGDVersionItem:
        flat = self._extract_item_data(item_data)
        apa = flat.get("ArtikelParagraphAnlage", [])
        sections = []
        if isinstance(apa, str):
            sections.append({
                "section_type": "Paragraf" if apa.startswith("§") else "Artikel",
                "section_id": apa,
                "api_id": "",
            })
        else:
            for entry in apa:
                sections.append({
                    "section_type": entry.get("Typ", ""),
                    "section_id": entry.get("Bezeichnung", ""),
                    "api_id": entry.get("Id", ""),
                })
        gsn = flat.get("Gesetzesnummer", "")
        fassung_vom = flat.get("Inkrafttretensdatum", "")
        if gsn and fassung_vom:
            ris_url = (
                f"https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
                f"Abfrage=Bundesnormen&Gesetzesnummer={gsn}&FassungVom={fassung_vom}"
            )
        else:
            ris_url = flat.get("GesamteRechtsvorschriftUrl", "")
        return OGDVersionItem(
            fassung_vom=fassung_vom,
            aenderung=flat.get("Aenderung", ""),
            kundmachungsorgan=flat.get("Kundmachungsorgan", ""),
            ris_url=ris_url,
            sections=sections,
        )

    def _extract_unique_dates(self, items: list) -> list:
        seen = set()
        unique = []
        for item in items:
            date = item.get("Inkrafttretensdatum", "")
            if date and date not in seen:
                seen.add(date)
                unique.append(date)
        return unique

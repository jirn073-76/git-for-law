"""Wayback Machine content fetcher for RIS GeltendeFassung pages."""

import json
from dataclasses import dataclass
from enum import Enum


class ContentSource(str, Enum):
    WAYBACK = "wayback"
    OGD_METADATA_ONLY = "ogd_metadata_only"
    MANUAL = "manual"


@dataclass
class CDXResult:
    """A single CDX API snapshot result."""

    urlkey: str = ""
    timestamp: str = ""
    original: str = ""
    mimetype: str = ""
    status_code: str = ""
    digest: str = ""
    length: str = ""


class WaybackFetcher:
    """Fetches RIS HTML content via Wayback Machine snapshots.

    Never accesses www.ris.bka.gv.at directly.
    """

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self._user_agent = "GitForLaw/1.0"
        self._follow_redirects = True
        self._ris_direct_access_enabled = False
        self._cdx_supports_ris_urls = False
        self._default_strategy = "direct_wayback"

    @property
    def user_agent(self) -> str:
        return self._user_agent

    @property
    def follow_redirects(self) -> bool:
        return self._follow_redirects

    @property
    def ris_direct_access_enabled(self) -> bool:
        return self._ris_direct_access_enabled

    @property
    def cdx_supports_ris_urls(self) -> bool:
        return self._cdx_supports_ris_urls

    @property
    def default_strategy(self) -> str:
        return self._default_strategy

    def _fassung_vom_to_wayback_timestamp(self, fassung_vom: str) -> str:
        """Convert YYYY-MM-DD to YYYYMMDDhhmmss (with noon default)."""
        date_part = fassung_vom.replace("-", "")
        return f"{date_part}120000"

    def _build_wayback_url_from_ris(self, ris_url: str, fassung_vom: str) -> str:
        ts = self._fassung_vom_to_wayback_timestamp(fassung_vom)
        return f"https://web.archive.org/web/{ts}id_/{ris_url}"

    def _generate_wayback_urls(self, ris_url: str, fassung_vom: str) -> list:
        return [self._build_wayback_url_from_ris(ris_url, fassung_vom)]

    def _build_fetch_headers(self) -> dict:
        return {"User-Agent": self._user_agent}

    def _warn_if_no_user_agent(self, headers: dict) -> bool:
        return headers.get("User-Agent", "") == ""

    def _is_redirect(self, status_code: int) -> bool:
        return status_code in (301, 302, 303, 307, 308)

    def _choose_fetch_strategy(self, ris_url: str) -> str:
        if "ris.bka.gv.at" in ris_url or "GeltendeFassung" in ris_url:
            return "direct_wayback"
        return "cdx"

    def _parse_cdx_response(self, response) -> list:
        if not isinstance(response, list):
            raise Exception("CDX response is not a list")
        if len(response) <= 1:
            return []
        rows = response[1:]
        results = []
        for row in rows:
            result = CDXResult(
                urlkey=row[0] if len(row) > 0 else "",
                timestamp=row[1] if len(row) > 1 else "",
                original=row[2] if len(row) > 2 else "",
                mimetype=row[3] if len(row) > 3 else "",
                status_code=row[4] if len(row) > 4 else "",
                digest=row[5] if len(row) > 5 else "",
                length=row[6] if len(row) > 6 else "",
            )
            if result.status_code == "200":
                results.append(result)
        return results

    def _parse_cdx_response_text(self, text) -> list:
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            raise Exception("Failed to parse CDX API response")
        return self._parse_cdx_response(data)

    def _process_wayback_content(self, content: str) -> str:
        if not content:
            return ""
        return content

    def _determine_content_source(self, strategy: str, content: str) -> ContentSource:
        if strategy == "direct_wayback":
            if content:
                return ContentSource.WAYBACK
            return ContentSource.OGD_METADATA_ONLY
        if content:
            return ContentSource.WAYBACK
        return ContentSource.OGD_METADATA_ONLY

    def fetch_content(self, ris_url: str, fassung_vom: str) -> dict:
        strategy = self._choose_fetch_strategy(ris_url)
        content = ""
        content_available = False

        if strategy == "direct_wayback":
            import requests
            urls = self._generate_wayback_urls(ris_url, fassung_vom)
            for url in urls:
                try:
                    resp = requests.get(
                        url,
                        headers=self._build_fetch_headers(),
                        timeout=self.timeout,
                        allow_redirects=True,
                    )
                    if resp.status_code == 200 and resp.text:
                        content = resp.text
                        content_available = True
                        break
                except requests.RequestException:
                    pass
        else:
            import requests
            try:
                cdx_url = "https://web.archive.org/cdx/search/cdx"
                params = {"url": ris_url, "output": "json"}
                resp = requests.get(
                    cdx_url,
                    params=params,
                    headers=self._build_fetch_headers(),
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    results = self._parse_cdx_response(resp.json())
                    if results:
                        ts = results[0].timestamp
                        wayback_url = f"https://web.archive.org/web/{ts}id_/{ris_url}"
                        resp2 = requests.get(
                            wayback_url,
                            headers=self._build_fetch_headers(),
                            timeout=self.timeout,
                            allow_redirects=True,
                        )
                        if resp2.status_code == 200 and resp2.text:
                            content = resp2.text
                            content_available = True
            except requests.RequestException:
                pass

        source = self._determine_content_source(strategy, content)
        return {
            "source": source.value,
            "content": content,
            "content_available": content_available,
            "strategy": strategy,
        }

    def fetch_law(self, law_abbrev: str, fassung_vom: str, ris_url: str) -> dict:
        result = self.fetch_content(ris_url=ris_url, fassung_vom=fassung_vom)
        result["law_abbrev"] = law_abbrev
        return result

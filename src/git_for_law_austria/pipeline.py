"""Pipeline orchestration for git-for-law-austria."""

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from git_for_law_austria.fetcher import OGDFetcher
from git_for_law_austria.ogd_content_fetcher import OGDContentFetcher
from git_for_law_austria.wayback_fetcher import WaybackFetcher
from git_for_law_austria.ris_parser import RISParser

CACHE_DIR = Path("data/html_cache")


class ContentCache:
    """File-based cache for RIS HTML responses. Avoids re-fetching on parser changes."""

    def __init__(self, cache_dir: Path = CACHE_DIR):
        self.cache_dir = cache_dir

    def _path(self, gsn: str, fassung_vom: str) -> Path:
        return self.cache_dir / gsn / f"{fassung_vom}.html"

    def get(self, gsn: str, fassung_vom: str) -> Optional[str]:
        p = self._path(gsn, fassung_vom)
        if p.exists():
            return p.read_text(encoding="utf-8")
        return None

    def put(self, gsn: str, fassung_vom: str, html: str) -> None:
        p = self._path(gsn, fassung_vom)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html, encoding="utf-8")

    def has(self, gsn: str, fassung_vom: str) -> bool:
        return self._path(gsn, fassung_vom).exists()

    def stats(self, gsn: str) -> tuple[int, int]:
        """Return (cached_count, total_count) for a GSN."""
        gsn_dir = self.cache_dir / gsn
        if not gsn_dir.exists():
            return 0, 0
        return len(list(gsn_dir.glob("*.html"))), 0


GSN_TO_ABBREV = {
    "10001622": "ABGB",
    "10000138": "B-VG",
    "10008115": "VBG",
}


_abbrev_cache = None


def load_abbrev_map(path: str = "data/gsn_to_abbrev.json") -> dict:
    """Load additional GSN→abbreviation mappings from a JSON file (cached)."""
    global _abbrev_cache
    if _abbrev_cache is not None:
        return _abbrev_cache
    import json as _json
    try:
        with open(path) as f:
            _abbrev_cache = _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError):
        _abbrev_cache = {}
    return _abbrev_cache


@dataclass
class PipelineResult:
    """Result of a pipeline run."""

    law_abbrev: str = ""
    gsn: str = ""
    versions_processed: int = 0
    versions_committed: int = 0
    sections_parsed: int = 0
    errors: list = field(default_factory=list)


class Pipeline:
    """Orchestrates the full metadata → content → parse → commit pipeline."""

    def __init__(self, repo_path: Optional[Path] = None, cache: Optional[ContentCache] = None):
        self.repo_path = repo_path
        self._committed_dates: set = set()
        self._fetcher: Optional[OGDFetcher] = None
        self._wayback: Optional[WaybackFetcher] = None
        self._parser: Optional[RISParser] = None
        self._content_fetcher: Optional[OGDContentFetcher] = None
        self._cache = cache if cache is not None else ContentCache()

    def _get_default_repo_path(self, abbrev: str) -> Path:
        base = os.environ.get("GIT_FOR_LAW_REPO_BASE", "data")
        return Path(base) / "laws" / abbrev

    def _get_fetcher(self) -> OGDFetcher:
        if self._fetcher is None:
            self._fetcher = OGDFetcher()
        return self._fetcher

    def _get_wayback(self) -> WaybackFetcher:
        if self._wayback is None:
            self._wayback = WaybackFetcher()
        return self._wayback

    def _get_parser(self) -> RISParser:
        if self._parser is None:
            self._parser = RISParser()
        return self._parser

    def _get_content_fetcher(self) -> OGDContentFetcher:
        if self._content_fetcher is None:
            self._content_fetcher = OGDContentFetcher()
        return self._content_fetcher

    def _abbrev_for_gsn(self, gsn: str) -> str:
        abbrev = GSN_TO_ABBREV.get(gsn)
        if abbrev:
            return abbrev
        extra = load_abbrev_map()
        return extra.get(gsn, gsn)

    # ── Helper methods kept for test compatibility ────────────────────

    def _normalize_date(self, date_str: str) -> str:
        return date_str

    def _is_future_date(self, date_str: str) -> bool:
        from datetime import date

        try:
            d = date.fromisoformat(date_str)
            return d > date.today()
        except (ValueError, TypeError):
            return False

    def _should_process_version(self, version: dict) -> bool:
        return True

    def _is_already_committed(self, fassung_vom: str, committed: set) -> bool:
        return fassung_vom in committed

    def _should_write_section(self, section: dict) -> bool:
        return bool(section.get("body", ""))

    def _should_commit_version(self, sections: list) -> bool:
        return any(self._should_write_section(s) for s in sections)

    def _section_to_json(self, section: dict) -> str:
        return json.dumps(section, ensure_ascii=False, indent=2)

    def _get_section_file_paths(self, sections: list) -> list:
        paths = []
        for section in sections:
            filename = self._section_id_to_filename(section["section_id"])
            paths.append(filename)
        return paths

    def _group_by_fassung_vom(self, items: list) -> dict:
        groups = {}
        for item in items:
            date_key = item.fassung_vom
            if date_key not in groups:
                groups[date_key] = []
            groups[date_key].append(item)
        return groups

    def _build_commit_message(self, abbrev: str, fassung_vom: str, aenderung: str) -> str:
        aenderung_clean = aenderung[:120] if len(aenderung) > 120 else aenderung
        return f"{abbrev} [{fassung_vom}]: {aenderung_clean}"

    @staticmethod
    def _last_amendment_from_kundmachung(kundm: str) -> str:
        """Extract the most recent BGBl amendment reference from a Kundmachungsorgan string.

        Examples:
        'BGBl. I Nr. 84/2005 zuletzt geändert durch BGBl. I Nr. 142/2006'
        -> 'BGBl. I Nr. 142/2006'
        'BGBl. I Nr. 84/2005 aufgehoben durch BGBl. I Nr. 83/2016'
        -> 'aufgehoben durch BGBl. I Nr. 83/2016'
        """
        if not kundm or not isinstance(kundm, str):
            return ""
        for sep in ("zuletzt geändert durch ", "aufgehoben durch "):
            idx = kundm.find(sep)
            if idx != -1:
                return kundm[idx + len(sep):].strip()
        return kundm.strip()

    def _section_id_to_filename(self, section_id: str) -> str:
        result = section_id.replace("§ ", "§_").replace(". ", "_").replace(" ", "_")
        return f"{result}.json"

    def _init_repo(self) -> Path:
        if self.repo_path is None:
            raise RuntimeError("repo_path not set")
        repo = Path(self.repo_path)
        repo.mkdir(parents=True, exist_ok=True)
        git_dir = repo / ".git"
        if not git_dir.exists():
            import git as gitlib

            gitlib.Repo.init(str(repo))
        return repo

    def _fetch_metadata(self, gsn: str, max_pages: int = 30) -> list:
        fetcher = self._get_fetcher()
        per_page = "OneHundred"
        items = []
        page = 1
        while page <= max_pages:
            body = fetcher._build_request_body(gsn=gsn, page=page, per_page=per_page)
            try:
                resp = fetcher._rate_limited_request("POST", fetcher.api_url, data=body)
                fetcher._handle_response(resp)
                data = fetcher._parse_response_text(resp.text)
                batch = fetcher._parse_response(data)
                if not batch:
                    break
                items.extend(batch)
                page += 1
            except Exception:
                if page == 1:
                    raise
                break
        return items

    def _acquire_content(self, items: list) -> dict:
        wayback = self._get_wayback()
        content_by_date = {}
        for item in items:
            if hasattr(item, "fassung_vom"):
                date_key = item.fassung_vom
            else:
                date_key = item.get("fassung_vom", "")
            if not date_key or date_key in content_by_date:
                continue
            if hasattr(item, "ris_url"):
                ris_url = item.ris_url
            else:
                ris_url = item.get("ris_url", "")
            if not ris_url:
                content_by_date[date_key] = ""
                continue
            try:
                result = wayback.fetch_content(
                    ris_url=ris_url, fassung_vom=date_key
                )
                content_by_date[date_key] = result.get("content", "")
            except Exception:
                content_by_date[date_key] = ""
        return content_by_date

    def _parse_content(self, content_by_date: dict, sections_by_date: dict = None) -> list:
        if sections_by_date is None:
            sections_by_date = {}
        parser = self._get_parser()
        parsed = []
        for date_key, html_text in content_by_date.items():
            if html_text:
                try:
                    sections = parser.parse_html(html_text, fassung_vom=date_key)
                except Exception:
                    sections = []
            else:
                sections = sections_by_date.get(date_key, [])
            for sec in sections:
                if isinstance(sec, dict):
                    sec.setdefault("fassung_vom", date_key)
                elif hasattr(sec, "fassung_vom"):
                    pass
            parsed.append({"fassung_vom": date_key, "sections": sections})
        return parsed

    def _fetch_geltende_fassung(self, gsn: str, dates: list) -> list:
        """Fetch and parse full legal text from GeltendeFassung.wxe for each date.

        HTML responses are cached to disk so parser fixes can be replayed
        without re-fetching from RIS.
        """
        parser = self._get_parser()
        parsed = []
        total = len(dates)
        cache_hits = 0
        for i, date_key in enumerate(dates):
            html_text = self._cache.get(gsn, date_key)
            if html_text is not None:
                cache_hits += 1
                sections = parser.parse_html(html_text, fassung_vom=date_key)
                parsed.append({"fassung_vom": date_key, "sections": sections})
                continue

            gf_url = (
                f"https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
                f"Abfrage=Bundesnormen&Gesetzesnummer={gsn}&FassungVom={date_key}"
            )
            try:
                import requests as _requests
                resp = _requests.get(
                    gf_url,
                    headers={"User-Agent": "GitForLaw/1.0"},
                    timeout=30,
                )
                if resp.status_code == 200:
                    self._cache.put(gsn, date_key, resp.text)
                    sections = parser.parse_html(resp.text, fassung_vom=date_key)
                    parsed.append({"fassung_vom": date_key, "sections": sections})
                else:
                    parsed.append({"fassung_vom": date_key, "sections": []})
            except Exception:
                parsed.append({"fassung_vom": date_key, "sections": []})
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{total} versions processed ({cache_hits} cached)...")
        if cache_hits > 0:
            print(f"  Cache hits: {cache_hits}/{total}")
        return parsed

    def _commit_versions(self, abbrev: str, parsed: list, metadata_by_date: dict) -> int:
        import git as gitlib

        repo_dir = self._init_repo()
        try:
            repo = gitlib.Repo(str(repo_dir))
        except gitlib.InvalidGitRepositoryError:
            repo = gitlib.Repo.init(str(repo_dir))

        committed = 0
        for entry in parsed:
            fassung_vom = entry["fassung_vom"]
            sections = entry["sections"]
            if fassung_vom in self._committed_dates:
                continue
            if not sections:
                continue

            meta = metadata_by_date.get(fassung_vom, {})
            aenderung = meta.get("aenderung", "")
            msg = self._build_commit_message(abbrev, fassung_vom, aenderung)

            fassung = {}
            section_n_counter = 0
            for sec in sections:
                if isinstance(sec, dict):
                    sid = sec.get("section_id") or ""
                    if not sid or sid == "Text":
                        section_n_counter += 1
                        sid = f"Section-{section_n_counter}"
                    fassung[sid] = sec
                else:
                    sid = sec.section_id
                    if not sid or sid == "Text":
                        section_n_counter += 1
                        sid = f"Section-{section_n_counter}"
                    fassung[sid] = sec.to_dict()
            (repo_dir / "fassung.json").write_text(
                json.dumps(fassung, ensure_ascii=False, indent=2) + "\n"
            )

            try:
                repo.git.add(all=True)
                repo.index.commit(msg)
            except Exception:
                continue

            self._committed_dates.add(fassung_vom)
            committed += 1

        return committed

    def run(
        self, gsn: str, max_versions: Optional[int] = None
    ) -> PipelineResult:
        abbrev = self._abbrev_for_gsn(gsn)
        if self.repo_path is None:
            self.repo_path = self._get_default_repo_path(abbrev)

        result = PipelineResult(law_abbrev=abbrev, gsn=gsn)

        print(f"Fetching metadata for {abbrev} (GSN {gsn})...")
        items = self._fetch_metadata(gsn)
        if not items:
            result.errors.append(f"No metadata found for GSN {gsn}")
            return result

        print(f"  Got {len(items)} version items from OGD API")

        grouped = self._group_by_fassung_vom(items)
        dates = sorted(grouped.keys())
        if max_versions is not None:
            dates = dates[:max_versions]

        result.versions_processed = len(dates)
        metadata_by_date = {}
        for date_key in dates:
            items_for_date = grouped[date_key]
            # Collect all sections from all items on this date
            all_sections = []
            seen_ids = set()
            for it in items_for_date:
                secs = getattr(it, "sections", None)
                if not isinstance(secs, list):
                    secs = []
                for s in secs:
                    sid = s.get("section_id", "") if isinstance(s, dict) else getattr(s, "section_id", "")
                    if sid and sid not in seen_ids:
                        seen_ids.add(sid)
                        all_sections.append(s)
            first = items_for_date[0]
            # Prefer aenderung from a Norm entry (Paragraph entries always have empty aenderung)
            aenderung = ""
            for it in items_for_date:
                aend = getattr(it, "aenderung", "")
                if aend:
                    aenderung = aend
                    break
            # Fallback: extract last amendment from Kundmachungsorgan on paragraph entries
            if not aenderung:
                seen_kundm = set()
                for it in items_for_date:
                    kundm = getattr(it, "kundmachungsorgan", "")
                    if isinstance(kundm, str) and kundm and kundm not in seen_kundm:
                        seen_kundm.add(kundm)
                        amendment = self._last_amendment_from_kundmachung(kundm)
                        if amendment:
                            aenderung = amendment
                            break
            ris_url = getattr(first, "ris_url", "")
            if not ris_url:
                ris_url = (
                    f"https://www.ris.bka.gv.at/GeltendeFassung.wxe?"
                    f"Abfrage=Bundesnormen&Gesetzesnummer={gsn}&FassungVom={date_key}"
                )
            metadata_by_date[date_key] = {
                "aenderung": aenderung,
                "ris_url": ris_url,
                "sections": all_sections,
            }

        sections_by_date = {}
        for date_key in dates:
            raw_sections = metadata_by_date[date_key]["sections"]
            sections_by_date[date_key] = [
                {
                    "section_id": s["section_id"] if isinstance(s, dict) else getattr(s, "section_id", ""),
                    "heading": s["section_id"] if isinstance(s, dict) else getattr(s, "section_id", ""),
                    "section_type": s.get("section_type", "") if isinstance(s, dict) else getattr(s, "section_type", ""),
                    "body": "",
                    "fassung_vom": date_key,
                }
                for s in raw_sections
            ]

        print(f"  {len(dates)} unique fassung_vom dates")

        print("Fetching full legal text from GeltendeFassung.wxe...")
        parsed = self._fetch_geltende_fassung(gsn, dates)

        total_sections = sum(len(e["sections"]) for e in parsed)
        sections_with_body = sum(
            1 for e in parsed for s in e["sections"] if len(s.body) > 10
        )
        result.sections_parsed = total_sections
        print(f"  {sections_with_body}/{total_sections} sections have full text")

        print(f"Committing to git repo: {self.repo_path}")
        committed = self._commit_versions(abbrev, parsed, metadata_by_date)
        result.versions_committed = committed
        print(f"  {committed} versions committed")

        return result

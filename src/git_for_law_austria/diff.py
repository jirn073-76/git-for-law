"""CLI diff viewer for git-for-law-austria law repos.

Usage: python3 -m git_for_law_austria.diff ABGB 2017-01-01 2018-01-01
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SectionDiff:
    """Diff between two versions of a single section."""

    section_id: str
    heading: str
    old_body: Optional[str]
    new_body: Optional[str]
    added_lines: list = field(default_factory=list)
    removed_lines: list = field(default_factory=list)

    def __post_init__(self):
        if self.old_body != self.new_body:
            if self.old_body is not None and self.old_body:
                self.removed_lines = [self.old_body]
            if self.new_body is not None and self.new_body:
                self.added_lines = [self.new_body]

    @property
    def is_changed(self) -> bool:
        return self.old_body != self.new_body


@dataclass
class DiffResult:
    """Result of diffing two law versions."""

    law_abbrev: str
    from_date: str
    to_date: str
    changed_sections: list = field(default_factory=list)
    unchanged_sections: list = field(default_factory=list)
    _unchanged_section_bodies: list = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        return len(self.changed_sections)

    @property
    def has_changes(self) -> bool:
        return len(self.changed_sections) > 0

    def to_json(self) -> str:
        data = {
            "law_abbrev": self.law_abbrev,
            "from_date": self.from_date,
            "to_date": self.to_date,
            "changed_sections": [
                {
                    "section_id": s.section_id,
                    "heading": s.heading,
                    "old_body": s.old_body,
                    "new_body": s.new_body,
                }
                for s in self.changed_sections
            ],
            "unchanged_sections": self.unchanged_sections,
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def to_ansi(self) -> str:
        GREEN = "\033[32m"
        RED = "\033[31m"
        BOLD = "\033[1m"
        DIM = "\033[2m"
        RESET = "\033[0m"

        lines = []
        lines.append(
            f"{BOLD}{self.law_abbrev}: {self.from_date} → {self.to_date}{RESET}"
        )
        lines.append("")

        for section in self.changed_sections:
            old_str = section.old_body or ""
            new_str = section.new_body or ""
            lines.append(f"{BOLD}── {section.heading}{RESET}")
            if old_str:
                lines.append(f"{RED}- {old_str}{RESET}")
            if new_str:
                lines.append(f"{GREEN}+ {new_str}{RESET}")
            lines.append("")

        # Show unchanged section bodies so the user can see the actual law text
        for section in self._unchanged_section_bodies:
            lines.append(f"{DIM}── {section['heading']} (unchanged) ──{RESET}")
            lines.append(f"{DIM}{section['body']}{RESET}")
            lines.append("")

        if not self.changed_sections and not self._unchanged_section_bodies:
            lines.append("No sections found in either version.")
        elif not self.changed_sections:
            if self._unchanged_section_bodies:
                lines.append("All sections unchanged.")
            else:
                lines.append("No changes.")

        lines.append(
            f"{self.total_changes} changed, {len(self.unchanged_sections)} unchanged"
        )
        return "\n".join(lines)

    def to_plain(self) -> str:
        lines = []
        lines.append(f"{self.law_abbrev}: {self.from_date} → {self.to_date}")
        lines.append("")

        for section in self.changed_sections:
            lines.append(f"── {section.heading} ──")
            if section.old_body:
                lines.append(f"- {section.old_body}")
            if section.new_body:
                lines.append(f"+ {section.new_body}")
            lines.append("")

        for section in self._unchanged_section_bodies:
            lines.append(f"── {section['heading']} (unchanged) ──")
            lines.append(f"{section['body']}")
            lines.append("")

        if not self.changed_sections and not self._unchanged_section_bodies:
            lines.append("No sections found in either version.")
        elif not self.changed_sections:
            if self._unchanged_section_bodies:
                lines.append("All sections unchanged.")
            else:
                lines.append("No changes.")

        lines.append(
            f"{self.total_changes} changed, {len(self.unchanged_sections)} unchanged"
        )
        return "\n".join(lines)

    def summary(self) -> dict:
        return {
            "law_abbrev": self.law_abbrev,
            "from_date": self.from_date,
            "to_date": self.to_date,
            "total_sections": self.total_changes + len(self.unchanged_sections),
            "changed_count": self.total_changes,
            "unchanged_count": len(self.unchanged_sections),
        }


class _RepoPath(str):
    """String that also supports Path-like operations for test compatibility."""

    def mkdir(self, *args, **kwargs):
        Path(self).mkdir(*args, **kwargs)

    def __truediv__(self, other):
        return Path(self) / other

    @property
    def parent(self):
        return Path(self).parent

    def exists(self):
        return Path(self).exists()

    def glob(self, pattern):
        return Path(self).glob(pattern)


class DiffViewer:
    """Reads section JSON from a git repo and computes diffs between versions."""

    ANSI_RESET = "\033[0m"
    ANSI_GREEN = "\033[32m"
    ANSI_RED = "\033[31m"
    ANSI_BOLD = "\033[1m"
    COLOR_RESET = ANSI_RESET

    def __init__(self, repo_path: str = ""):
        p = repo_path if repo_path else "data/laws/ABGB"
        self._repo_path = _RepoPath(p)

    @property
    def repo_path(self):
        return self._repo_path

    @repo_path.setter
    def repo_path(self, value):
        self._repo_path = _RepoPath(str(value))

    def _resolve_repo_path(self) -> Path:
        return Path(str(self._repo_path))

    def _is_git_repo(self) -> bool:
        git_dir = self._resolve_repo_path() / ".git"
        return git_dir.exists()

    def _git_log(self) -> list:
        repo = self._resolve_repo_path()
        try:
            output = subprocess.check_output(
                ["git", "-C", str(repo), "log", "--format=%H %s"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return []
        commits = []
        for line in output.strip().split("\n"):
            if not line:
                continue
            commits.append(line)
        return commits

    def _git_show(self, commit_ref: str, filepath: str) -> Optional[str]:
        repo = self._resolve_repo_path()
        try:
            output = subprocess.check_output(
                ["git", "-C", str(repo), "show", f"{commit_ref}:{filepath}"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            return output
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    def _find_commit_for_date(self, fassung_vom: str) -> Optional[str]:
        commits = self._git_log()
        pattern = re.compile(rf"\[{re.escape(fassung_vom)}\]")
        for line in commits:
            if pattern.search(line):
                parts = line.split(" ", 1)
                if parts:
                    return parts[0]  # Return the hash, not the full message
        return None

    def _list_tracked_json_files(self, commit_ref: str) -> list:
        repo = self._resolve_repo_path()
        try:
            output = subprocess.check_output(
                ["git", "-C", str(repo), "ls-tree", "--name-only", "-r", "-z", commit_ref],
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return []
        files = []
        for name in output.split(b"\x00"):
            name = name.decode("utf-8", errors="replace")
            if name and name.endswith(".json"):
                files.append(name)
        return sorted(files)

    def _parse_commit_message(self, msg: str) -> Optional[dict]:
        m = re.match(r"^(?:\S{7,}\s+)?(\S+)\s+\[([^\]]+)\]:", msg)
        if m:
            return {"abbrev": m.group(1), "fassung_vom": m.group(2)}
        return None

    def read_section(self, section_id: str) -> Optional[dict]:
        repo = self._resolve_repo_path()
        section_file = repo / f"{section_id}.json"
        if not section_file.exists():
            return None
        try:
            return json.loads(section_file.read_text())
        except (json.JSONDecodeError, Exception):
            raise Exception(f"Failed to parse JSON for section {section_id}")

    def list_sections(self) -> list:
        repo = self._resolve_repo_path()
        fassung_file = repo / "fassung.json"
        if not fassung_file.exists():
            return []
        try:
            fassung = json.loads(fassung_file.read_text())
            return sorted(fassung.keys())
        except (json.JSONDecodeError, OSError):
            return []

    def list_versions(self) -> list:
        commits = self._git_log()
        versions = []
        for msg in commits:
            info = self._parse_commit_message(msg)
            if info:
                versions.append(info["fassung_vom"])
        return sorted(set(versions))

    def get_version_sections(self, fassung_vom: str) -> dict:
        if self._is_git_repo():
            commit_ref = self._find_commit_for_date(fassung_vom)
            if not commit_ref:
                return {}
            # New format: single fassung.json
            content = self._git_show(commit_ref, "fassung.json")
            if content is not None:
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    pass
            # Old format: per-section JSON files
            files = self._list_tracked_json_files(commit_ref)
            sections = {}
            for f in files:
                content = self._git_show(commit_ref, f)
                if content is None:
                    continue
                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    continue
                sid = data.get("section_id", Path(f).stem)
                sections[sid] = data
            return sections
        # Filesystem fallback (for tests / non-git repos)
        fassung_file = self._resolve_repo_path() / "fassung.json"
        if fassung_file.exists():
            try:
                return json.loads(fassung_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def diff(
        self,
        from_date: str,
        to_date: str,
        section_id: Optional[str] = None,
        section_type: Optional[str] = None,
    ) -> DiffResult:
        repo = self._resolve_repo_path()
        abbrev = repo.name
        if not self._is_git_repo() and not (repo / "fassung.json").exists():
            return DiffResult(
                law_abbrev=abbrev,
                from_date=from_date,
                to_date=to_date,
                changed_sections=[],
                unchanged_sections=[],
            )
        if from_date == to_date:
            old_sections = self.get_version_sections(from_date)
            unchanged = [s.get("section_id", s.get("heading", k))
                         for k, s in old_sections.items()]
            return DiffResult(
                law_abbrev=abbrev,
                from_date=from_date,
                to_date=to_date,
                changed_sections=[],
                unchanged_sections=unchanged,
            )

        old = self.get_version_sections(from_date)
        new = self.get_version_sections(to_date)

        if not old and not new:
            raise ValueError(
                f"Neither version found: {from_date} or {to_date}"
            )
        if not old:
            raise ValueError(f"Version not found: {from_date}")
        if not new:
            raise ValueError(f"Version not found: {to_date}")

        all_ids = set(old.keys()) | set(new.keys())
        changed = []
        unchanged = []
        unchanged_bodies = []

        for sid in sorted(all_ids):
            if section_id is not None and sid != section_id:
                continue
            old_sec = old.get(sid, {})
            new_sec = new.get(sid, {})
            if section_type is not None:
                old_type = old_sec.get("section_type", "")
                new_type = new_sec.get("section_type", "")
                if old_type != section_type and new_type != section_type:
                    continue

            old_body = old_sec.get("body", "")
            new_body = new_sec.get("body", "")
            heading = new_sec.get("heading", old_sec.get("heading", sid))
            sdiff = SectionDiff(
                section_id=sid,
                heading=heading,
                old_body=old_body if old_body else None,
                new_body=new_body if new_body else None,
            )
            if sdiff.is_changed:
                changed.append(sdiff)
            else:
                unchanged.append(sid)
                body = new_body or old_body
                if body:
                    unchanged_bodies.append({
                        "heading": heading,
                        "body": body,
                    })

        return DiffResult(
            law_abbrev=abbrev,
            from_date=from_date,
            to_date=to_date,
            changed_sections=changed,
            unchanged_sections=unchanged,
            _unchanged_section_bodies=unchanged_bodies,
        )


def diff_law(
    abbrev: str,
    from_date: str,
    to_date: str,
    section_id: Optional[str] = None,
) -> DiffResult:
    """Convenience function: diff two versions of a law."""
    base = os.environ.get("GIT_FOR_LAW_REPO_BASE", "data")
    repo_path = Path(base) / "laws" / abbrev
    viewer = DiffViewer(repo_path=str(repo_path))
    return viewer.diff(from_date, to_date, section_id=section_id)


def _main():
    parser = argparse.ArgumentParser(
        prog="git_for_law_austria.diff",
        description="Diff two versions of an Austrian federal law",
    )
    parser.add_argument("law", help="Law abbreviation (e.g., ABGB)")
    parser.add_argument("from_date", help="From fassung_vom date (YYYY-MM-DD)")
    parser.add_argument("to_date", help="To fassung_vom date (YYYY-MM-DD)")
    parser.add_argument(
        "--section", "-s", help="Filter to specific section ID (e.g., §_1)"
    )
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")

    args = parser.parse_args()

    base = os.environ.get("GIT_FOR_LAW_REPO_BASE", "data")
    repo_path = Path(base) / "laws" / args.law
    if not repo_path.exists():
        known = [p.parent.name for p in Path(base).glob("laws/*/.git")]
        known = [k for k in known if k]
        hint = f" Known: {', '.join(sorted(known))}" if known else ""
        print(f"Error: No law repo found at {repo_path}.{hint}", file=sys.stderr)
        sys.exit(1)

    result = diff_law(args.law, args.from_date, args.to_date, section_id=args.section)

    if args.no_color:
        print(result.to_plain())
    else:
        print(result.to_ansi())


if __name__ == "__main__":
    _main()

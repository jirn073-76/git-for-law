"""Quality measurement harness for git-for-law-austria repos."""

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class QualityReport:
    """Quality assessment report for a law git repo."""

    law_abbrev: str
    content_quality: float
    commit_health: float
    diff_quality: float
    coverage: float
    overall_score: float
    details: dict = field(default_factory=dict)

    def to_json(self, indent: Optional[int] = None) -> str:
        data = {
            "law_abbrev": self.law_abbrev,
            "content_quality": self.content_quality,
            "commit_health": self.commit_health,
            "diff_quality": self.diff_quality,
            "coverage": self.coverage,
            "overall_score": self.overall_score,
            "details": self.details,
        }
        return json.dumps(data, indent=indent)


class QualityHarness:
    """Measures quality of a git-for-law-austria repo across four dimensions.

    Dimensions and weights:
    - content_quality (0.4): % sections with >50 chars body
    - commit_health (0.3): % non-duplicate, non-empty-diff commits
    - diff_quality (0.2): avg diff size normalized to [0,1]
    - coverage (0.1): % expected versions actually committed
    """

    def __init__(self):
        self.content_quality_weight = 0.4
        self.commit_health_weight = 0.3
        self.diff_quality_weight = 0.2
        self.coverage_weight = 0.1

    def _calculate_content_quality(self, sections: list) -> float:
        if not sections:
            return 0.0
        long_count = sum(1 for s in sections if len(s.get("body", "")) > 50)
        return long_count / len(sections)

    def _calculate_commit_health(self, commits: list, duplicate_count: int) -> float:
        if not commits:
            return 0.0
        empty_diff_count = sum(1 for c in commits if c.get("empty_diff", False))
        healthy = len(commits) - duplicate_count - empty_diff_count
        if healthy < 0:
            healthy = 0
        return healthy / len(commits)

    def _calculate_diff_quality(self, commits: list) -> float:
        if not commits:
            return 0.0
        total = 0.0
        for c in commits:
            lines = c.get("diff_lines", 0)
            normalized = min(lines / 40.0, 1.0)
            total += normalized
        return total / len(commits)

    def _calculate_coverage(self, committed: int, expected: int) -> float:
        if expected == 0:
            return 0.0
        return committed / expected

    def _calculate_overall(
        self,
        content_quality: float,
        commit_health: float,
        diff_quality: float,
        coverage: float,
    ) -> float:
        result = (
            self.content_quality_weight * content_quality
            + self.commit_health_weight * commit_health
            + self.diff_quality_weight * diff_quality
            + self.coverage_weight * coverage
        )
        return round(result, 10)

    def calculate(self, input_data: dict) -> QualityReport:
        law_abbrev = input_data.get("law_abbrev", "")
        sections = input_data.get("sections", [])
        commits = input_data.get("commits", [])
        duplicate_commits = input_data.get("duplicate_commits", 0)
        versions_expected = input_data.get("versions_expected", 0)

        content_quality = self._calculate_content_quality(sections)
        commit_health = self._calculate_commit_health(commits, duplicate_commits)
        diff_quality = self._calculate_diff_quality(commits)
        coverage = self._calculate_coverage(len(commits), versions_expected) if commits else 0.0
        overall = round(self._calculate_overall(
            content_quality, commit_health, diff_quality, coverage
        ), 10)

        details = {
            "sections_total": len(sections),
            "sections_long_body": sum(
                1 for s in sections if len(s.get("body", "")) > 50
            ),
            "commits_total": len(commits),
            "commits_duplicate": duplicate_commits,
            "commits_empty_diff": sum(
                1 for c in commits if c.get("empty_diff", False)
            ),
            "versions_expected": versions_expected,
            "versions_committed": len(commits),
        }

        return QualityReport(
            law_abbrev=law_abbrev,
            content_quality=content_quality,
            commit_health=commit_health,
            diff_quality=diff_quality,
            coverage=coverage,
            overall_score=overall,
            details=details,
        )

    def format_output(self, report_data: dict, json_mode: bool = False) -> str:
        if json_mode:
            report = QualityReport(
                law_abbrev=report_data.get("law_abbrev", ""),
                content_quality=report_data.get("content_quality", 0.0),
                commit_health=report_data.get("commit_health", 0.0),
                diff_quality=report_data.get("diff_quality", 0.0),
                coverage=report_data.get("coverage", 0.0),
                overall_score=report_data.get("overall_score", 0.0),
                details=report_data.get("details", {}),
            )
            return report.to_json(indent=2)
        else:
            lines = []
            lines.append(f"Law: {report_data.get('law_abbrev', '')}")
            lines.append(
                f"Overall Score: {report_data.get('overall_score', 0.0):.1%}"
            )
            lines.append(
                f"Content Quality: {report_data.get('content_quality', 0.0):.1%}"
            )
            lines.append(
                f"Commit Health: {report_data.get('commit_health', 0.0):.1%}"
            )
            lines.append(
                f"Diff Quality: {report_data.get('diff_quality', 0.0):.1%}"
            )
            lines.append(
                f"Coverage: {report_data.get('coverage', 0.0):.1%}"
            )
            details = report_data.get("details", {})
            if details:
                lines.append(f"  Sections total: {details.get('sections_total', 0)}")
                lines.append(
                    f"  Sections long body: {details.get('sections_long_body', 0)}"
                )
                lines.append(f"  Commits total: {details.get('commits_total', 0)}")
                lines.append(
                    f"  Versions expected: {details.get('versions_expected', 0)}"
                )
                lines.append(
                    f"  Versions committed: {details.get('versions_committed', 0)}"
                )
            return "\n".join(lines)

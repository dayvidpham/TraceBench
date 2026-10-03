"""Cutoff spec + resolution (C4 component: CutoffResolver)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from snapshot.peasant import PeasantClient


def parse_time(value: str) -> datetime:
    """Parse ISO-8601, normalizing to tz-aware UTC."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class Cutoff:
    """Either an absolute date or a PR number (resolved via peasant binary)."""

    kind: str  # "date" | "pr"
    date: datetime | None = None
    pr_number: int | None = None

    @staticmethod
    def by_date(value: str | datetime) -> "Cutoff":
        dt = parse_time(value) if isinstance(value, str) else value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return Cutoff(kind="date", date=dt.astimezone(timezone.utc))

    @staticmethod
    def by_pr(pr_number: int) -> "Cutoff":
        if pr_number <= 0:
            raise ValueError(f"PR number must be positive, got {pr_number}")
        return Cutoff(kind="pr", pr_number=pr_number)


def resolve_cutoff_time(
    cutoff: Cutoff,
    peasant: PeasantClient | None = None,
    pr_start_override: str | datetime | None = None,
) -> datetime:
    """Resolve a Cutoff to an absolute UTC timestamp.

    PR rule (issue #2 acceptance): snapshot contains strictly nothing at or
    after the PR start, so the cutoff time *is* the PR start (exclusive).
    Date rule: inclusive of the date second.
    """
    if cutoff.kind == "date":
        assert cutoff.date is not None
        return cutoff.date.astimezone(timezone.utc)
    if cutoff.kind == "pr":
        if pr_start_override is not None:
            dt = (
                parse_time(pr_start_override)
                if isinstance(pr_start_override, str)
                else pr_start_override
            )
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        if peasant is None:
            raise ValueError(
                "PR cutoff needs a PeasantClient (peasant binary) or "
                "pr_start_override=..."
            )
        assert cutoff.pr_number is not None
        return peasant.pr_start(cutoff.pr_number).astimezone(timezone.utc)
    raise ValueError(f"unknown cutoff kind: {cutoff.kind!r}")

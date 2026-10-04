"""Assemble Harbor task payloads from the corpus.

A task payload contains:

1. ``pr.json`` - the pull request the task is built from.
2. ``prior-traces/`` - every trace of the same codebase merged before the
   pull request, excluding the pull request's own sessions.
3. ``repo/`` - integration point for the repository tooling: the working tree
   at the pre-PR state. Left empty here; ``repo-request.json`` states exactly
   what must be materialized.
4. ``tests/`` - integration point for the repository tooling: every test file
   at the merged state (the golden suite). Left empty here.

Task skeletons (Harbor ``task.toml`` / ``instruction.md``) are generated
later from these payloads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .corpus import Corpus

#: Repository suffixes that belong to the same codebase family. The
#: prerelease archive is the live repository's pre-launch history, so a task
#: built from a live pull request sees archive traces as prior context.
ARCHIVE_SUFFIX = "-prerelease-archive"

#: Path patterns that count as tests when the golden suite is extracted at
#: the merged commit. The repository tooling materializes ``tests/`` from
#: these; kept here so the request is explicit and configurable.
DEFAULT_TEST_PATTERNS = (
    "**/*_test.go",
    "**/testdata/**",
    "**/*.test.ts",
    "**/*.test.tsx",
    "**/*.test.js",
    "**/*.test.jsx",
    "**/*.spec.ts",
    "**/*.spec.tsx",
    "**/*.spec.js",
    "**/*.spec.jsx",
)


def repo_family(repo: str) -> set[str]:
    """Return the repository and its codebase-family counterpart."""
    if repo.endswith(ARCHIVE_SUFFIX):
        return {repo, repo[: -len(ARCHIVE_SUFFIX)]}
    return {repo, repo + ARCHIVE_SUFFIX}


@dataclass(frozen=True)
class TaskPayload:
    """What a built task payload contains."""

    pr_id: str
    path: Path
    prior_pull_requests: int
    prior_traces: int
    prior_sessions: int
    cutoff_time: str
    merge_commit: str | None


class TaskBuilder:
    """Builds task payloads from a loaded corpus.

    ``pr_index`` optionally maps pull request ids to enriched records (the
    local ``corpus/index/merged_prs.json`` carries ``merge_commit``,
    ``head_oid``, ``created_at``, and ``base_ref``, which the published dump
    omits). Without it, the published records are used as-is.
    """

    def __init__(self, corpus: Corpus, pr_index: dict[str, dict[str, Any]] | None = None):
        self.corpus = corpus
        self.pr_index = pr_index or {}

    def enrich(self, pr: dict[str, Any]) -> dict[str, Any]:
        """Overlay the richer index record (merge commits, created_at) on the
        published record."""
        enriched = dict(pr)
        for key, value in self.pr_index.get(pr["id"], {}).items():
            if value is not None:
                enriched[key] = value
        return enriched

    def build(self, pr_id: str, dest: str | Path) -> TaskPayload:
        pr = self.corpus.pull_requests.get(pr_id)
        if pr is None:
            raise KeyError(f"pull request {pr_id} is not in the corpus")
        pr = self.enrich(pr)
        dest = Path(dest)
        (dest / "prior-traces" / "transcripts").mkdir(parents=True, exist_ok=True)
        (dest / "repo").mkdir(exist_ok=True)
        (dest / "tests").mkdir(exist_ok=True)

        cutoff_time = pr.get("created_at") or pr["merged_at"]
        family = repo_family(pr["repo"])
        own_sessions = {trace.session_id for trace in self.corpus.sessions_for_pr(pr_id)}

        prior_prs: list[dict[str, Any]] = []
        for candidate in self.corpus.prs():
            if candidate["id"] == pr_id or candidate.get("repo") not in family:
                continue
            merged_at = candidate.get("merged_at")
            if merged_at and merged_at < cutoff_time:
                prior_prs.append(candidate)
        prior_prs.sort(key=lambda record: record.get("merged_at", ""))

        traces: list[dict[str, Any]] = []
        session_ids: list[str] = []
        seen: set[str] = set()
        for candidate in prior_prs:
            for trace in self.corpus.sessions_for_pr(candidate["id"]):
                if trace.session_id in own_sessions:
                    continue
                traces.append(_trace_record(trace))
                if trace.session_id not in seen:
                    seen.add(trace.session_id)
                    session_ids.append(trace.session_id)
        session_ids.sort()

        metadata = [self.corpus.metadata[session_id] for session_id in session_ids if session_id in self.corpus.metadata]
        _write_json(dest / "pr.json", pr)
        _write_jsonl(dest / "prior-traces" / "traces.jsonl", traces)
        _write_jsonl(dest / "prior-traces" / "metadata.jsonl", metadata)
        for session_id in session_ids:
            source = self.corpus.root / "transcripts" / f"{session_id}.jsonl"
            if source.is_file():
                (dest / "prior-traces" / "transcripts" / f"{session_id}.jsonl").write_bytes(source.read_bytes())
        _write_json(
            dest / "prior-traces" / "manifest.json",
            {
                "source": "prior-traces",
                "cutoff": {"exclusive": True, "time": cutoff_time},
                "family": sorted(family),
                "pull_requests": [candidate["id"] for candidate in prior_prs],
                "traces": len(traces),
                "sessions": len(session_ids),
                "excluded_pr_sessions": sorted(own_sessions),
                "metadata_schema_version": self.corpus.manifest.get("metadata_schema_version"),
                "push_contract_version": self.corpus.manifest.get("push_contract_version"),
            },
        )

        merge_commit = pr.get("merge_commit")
        repo_request = {
            "pr": pr_id,
            "repo": pr["repo"],
            "number": pr["number"],
            "merge_commit": merge_commit,
            "base_commit": None,
            "base_commit_rule": "first parent of merge_commit",
            "cutoff": {"kind": "pr", "exclusive": True, "time": cutoff_time},
            "requires": {
                "repo/": "working tree at the pre-PR state (base_commit)",
                "tests/": "every test file at the merged state (merge_commit)",
            },
            "test_patterns": list(DEFAULT_TEST_PATTERNS),
            "note": (
                "Integration point for the repository tooling; materialize repo/ and "
                "tests/ here. The snapshot module (issue #2) resolves the same cutoff "
                "and materializes repository trees."
            ),
        }
        _write_json(dest / "repo-request.json", repo_request)
        _write_json(
            dest / "task.json",
            {
                "pr": pr_id,
                "split": pr.get("split"),
                "cutoff": {"exclusive": True, "time": cutoff_time},
                "prior_traces": len(traces),
                "prior_sessions": len(session_ids),
                "prior_pull_requests": len(prior_prs),
                "repo_request": "repo-request.json",
            },
        )
        return TaskPayload(
            pr_id=pr_id,
            path=dest,
            prior_pull_requests=len(prior_prs),
            prior_traces=len(traces),
            prior_sessions=len(session_ids),
            cutoff_time=cutoff_time,
            merge_commit=merge_commit,
        )


def load_pr_index(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load ``corpus/index/merged_prs.json`` keyed by pull request id."""
    records = json.loads(Path(path).read_text())
    index: dict[str, dict[str, Any]] = {}
    for record in records:
        pr_id = f"{record['repo']}#{record['number']}"
        index[pr_id] = record
    return index


def _trace_record(trace: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"pr": trace.pr, "session_id": trace.session_id, "method": trace.method}
    if trace.relation is not None:
        record["relation"] = trace.relation
    if trace.split is not None:
        record["split"] = trace.split
    return record


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")

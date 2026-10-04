"""Assemble Harbor task payloads from the corpus.

A task payload contains:

1. ``pr.json`` - the pull request the task is built from.
2. ``prior-traces/`` - every trace of the same codebase merged before the
   pull request's develop boundary, excluding the pull request's own sessions
   and any session that ran past the boundary.
3. ``repo/`` - integration point for the repository tooling: the working tree
   at the pre-PR state. Left empty here; ``repo-request.json`` states exactly
   what must be materialized.
4. ``tests/`` - integration point for the repository tooling: every test file
   at the merged state (the golden suite). Left empty here.

The prior traces cover the sampled pull requests only: the corpus is a sample,
not the repository's full history. ``develop`` keeps one commit per pull
request (squash-rebase), so the boundary is the parent of the pull request's
squash commit; ``--repo-dir`` resolves it by commit ancestry, and ``merged_at``
is the portable proxy otherwise. A merge commit is required: pass ``--index``
when the published dump record does not carry one.

Task skeletons (Harbor ``task.toml`` / ``instruction.md``) are generated
later from these payloads.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .corpus import Corpus
from .target_config import TargetConfiguration

#: Repository suffixes that belong to the same codebase family. The
#: prerelease archive is the live repository's pre-launch history, so a task
#: built from a live pull request sees archive traces as prior context.
ARCHIVE_SUFFIX = "-prerelease-archive"

#: Path patterns that count as tests when the golden suite is extracted at
#: the merged commit. The repository tooling materializes ``tests/`` from
#: these; kept here so the request is explicit and configurable. Doublestar
#: globs relative to the repository root.
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

#: Entries this tool owns inside a payload or task directory. ``--force``
#: clears exactly these; caller-authored files elsewhere are never touched.
GENERATED_ENTRIES = ("prior-traces", "repo", "tests", "pr.json", "task.json", "repo-request.json")

_GIT_BINARY = "git"


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
    cutoff_basis: str
    cutoff_time: str
    boundary_commit: str | None
    merge_commit: str
    sessions_past_cutoff: int
    missing_sessions: int
    target_config: str | None = None


class TaskBuilder:
    """Builds task payloads from a loaded corpus.

    ``pr_index`` maps pull request ids to enriched records (the local
    ``corpus/index/merged_prs.json`` carries ``merge_commit``, ``head_oid``,
    ``created_at``, and ``base_ref``, which the published dump omits). A
    merge commit is required for the target and for every candidate when
    ``repo_dir`` is set.

    ``repo_dir`` is an optional local clone used to resolve the develop
    boundary commit (``merge_commit^``) and to order prior pull requests by
    commit ancestry. Without it, ``merged_at`` is the portable proxy.
    """

    def __init__(
        self,
        corpus: Corpus,
        pr_index: dict[str, dict[str, Any]] | None = None,
        repo_dir: str | Path | None = None,
    ):
        self.corpus = corpus
        self.pr_index = pr_index or {}
        self.repo_dir = str(repo_dir) if repo_dir is not None else None

    def enrich(self, pr: dict[str, Any]) -> dict[str, Any]:
        """Overlay the richer index record (merge commits, dates) on the
        published record."""
        enriched = dict(pr)
        for key, value in self.pr_index.get(pr["id"], {}).items():
            if value is not None:
                enriched[key] = value
        return enriched

    def boundary(self, pr: dict[str, Any]) -> tuple[str, str | None, str, str]:
        """Resolve the develop boundary for a pull request.

        Returns ``(basis, boundary_commit, cutoff_time, merge_commit)``.
        """
        merge_commit = pr.get("merge_commit")
        if not merge_commit:
            raise ValueError(
                f"pull request {pr['id']} has no merge commit; pass --index with "
                "corpus/index/merged_prs.json (the published dump omits merge commits)"
            )
        if self.repo_dir:
            boundary = self._git("rev-parse", f"{merge_commit}^")
            cutoff_time = self._git("show", "-s", "--format=%cI", boundary)
            return "commit_ancestry", boundary, cutoff_time, merge_commit
        if not pr.get("merged_at"):
            raise ValueError(f"pull request {pr['id']} has no merged_at; pass --index")
        return "merged_at", None, pr["merged_at"], merge_commit

    def build(
        self,
        pr_id: str,
        dest: str | Path,
        *,
        force: bool = False,
        target_config: TargetConfiguration | None = None,
    ) -> TaskPayload:
        pr = self.corpus.pull_requests.get(pr_id)
        if pr is None:
            raise KeyError(f"pull request {pr_id} is not in the corpus")
        pr = self.enrich(pr)
        dest = Path(dest)
        if dest.exists() and any(dest.iterdir()):
            if not force:
                raise ValueError(
                    f"destination {dest} is not empty; choose a fresh directory or pass --force"
                )
            clear_generated(dest, GENERATED_ENTRIES)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "prior-traces" / "transcripts").mkdir(parents=True, exist_ok=True)
        (dest / "repo").mkdir(exist_ok=True)
        (dest / "tests").mkdir(exist_ok=True)

        basis, boundary_commit, cutoff_time, merge_commit = self.boundary(pr)
        cutoff_ms = _iso_to_ms(cutoff_time)
        family = repo_family(pr["repo"])
        own_sessions = {trace.session_id for trace in self.corpus.sessions_for_pr(pr_id)}

        prior_prs = self._prior_pull_requests(pr, family, basis, boundary_commit, cutoff_ms)
        traces: list[dict[str, Any]] = []
        session_ids: list[str] = []
        seen: set[str] = set()
        past_cutoff: list[str] = []
        for candidate in prior_prs:
            for trace in self.corpus.sessions_for_pr(candidate["id"]):
                if trace.session_id in own_sessions:
                    continue
                metadata = self.corpus.metadata.get(trace.session_id)
                end_ms = _session_end_ms(metadata)
                if end_ms is not None and end_ms > cutoff_ms:
                    if trace.session_id not in past_cutoff:
                        past_cutoff.append(trace.session_id)
                    continue
                traces.append(_trace_record(trace))
                if trace.session_id not in seen:
                    seen.add(trace.session_id)
                    session_ids.append(trace.session_id)
        session_ids.sort()

        missing: list[str] = []
        metadata_records: list[dict[str, Any]] = []
        materialized = 0
        for session_id in session_ids:
            record = self.corpus.metadata.get(session_id)
            if record is None:
                missing.append(session_id)
            else:
                metadata_records.append(record)
            source = self.corpus.root / "transcripts" / f"{session_id}.jsonl"
            if source.is_file():
                (dest / "prior-traces" / "transcripts" / f"{session_id}.jsonl").write_bytes(
                    source.read_bytes()
                )
                materialized += 1
            elif session_id not in missing:
                missing.append(session_id)

        _write_json(dest / "pr.json", pr)
        _write_jsonl(dest / "prior-traces" / "traces.jsonl", traces)
        _write_jsonl(dest / "prior-traces" / "metadata.jsonl", metadata_records)
        cutoff = {
            "basis": basis,
            "exclusive": True,
            "time": cutoff_time,
            "boundary_commit": boundary_commit,
            "merge_commit": merge_commit,
        }
        _write_json(
            dest / "prior-traces" / "manifest.json",
            {
                "source": "prior-traces",
                "sampled_scope": "traces of the sampled pull requests only",
                "cutoff": cutoff,
                "family": sorted(family),
                "pull_requests": [candidate["id"] for candidate in prior_prs],
                "traces": len(traces),
                "sessions": len(session_ids),
                "materialized_sessions": materialized,
                "excluded_pr_sessions": sorted(own_sessions),
                "sessions_past_cutoff": sorted(past_cutoff),
                "target_configuration": target_config.to_dict() if target_config else None,
                "missing_sessions": sorted(missing),
                "metadata_schema_version": self.corpus.manifest.get("metadata_schema_version"),
                "push_contract_version": self.corpus.manifest.get("push_contract_version"),
            },
        )
        repo_request = {
            "pr": pr_id,
            "repo": pr["repo"],
            "number": pr["number"],
            "merge_commit": merge_commit,
            "tree_commit": boundary_commit,
            "tree_commit_rule": "first parent of merge_commit (the develop commit before the PR)",
            "trace_cutoff": cutoff,
            "requires": {
                "repo/": "working tree at tree_commit (the pre-PR state)",
                "tests/": "every test file at merge_commit (the merged state)",
            },
            "test_patterns": list(DEFAULT_TEST_PATTERNS),
            "glob_dialect": "doublestar globs relative to the repository root",
            "merge_commit_policy": "required; pass --index when the corpus record lacks one",
            "note": (
                "Adapter required: the snapshot module resolves trees by cutoff time. "
                "Materialize repo/ from tree_commit and tests/ from merge_commit; do not "
                "reuse the trace cutoff for the tree."
            ),
        }
        _write_json(dest / "repo-request.json", repo_request)
        _write_json(
            dest / "task.json",
            {
                "pr": pr_id,
                "split": pr.get("split"),
                "cutoff": cutoff,
                "prior_traces": len(traces),
                "prior_sessions": len(session_ids),
                "prior_pull_requests": len(prior_prs),
                "sessions_past_cutoff": len(past_cutoff),
                "target_configuration": target_config.to_dict() if target_config else None,
                "missing_sessions": len(missing),
                "repo_request": "repo-request.json",
            },
        )
        return TaskPayload(
            pr_id=pr_id,
            path=dest,
            prior_pull_requests=len(prior_prs),
            prior_traces=len(traces),
            prior_sessions=len(session_ids),
            cutoff_basis=basis,
            cutoff_time=cutoff_time,
            boundary_commit=boundary_commit,
            merge_commit=merge_commit,
            sessions_past_cutoff=len(past_cutoff),
            missing_sessions=len(missing),
            target_config=target_config.name if target_config else None,
        )

    def _prior_pull_requests(
        self,
        pr: dict[str, Any],
        family: set[str],
        basis: str,
        boundary_commit: str | None,
        cutoff_ms: int,
    ) -> list[dict[str, Any]]:
        prior: list[dict[str, Any]] = []
        for candidate in self.corpus.prs():
            if candidate["id"] == pr["id"] or candidate.get("repo") not in family:
                continue
            same_repo = candidate.get("repo") == pr["repo"]
            if basis == "commit_ancestry" and same_repo:
                enriched = self.enrich(candidate)
                candidate_commit = enriched.get("merge_commit")
                if not candidate_commit:
                    raise ValueError(
                        f"pull request {candidate['id']} has no merge commit; the index "
                        "must cover every candidate when --repo-dir is used"
                    )
                if self._is_ancestor(candidate_commit, boundary_commit):
                    prior.append(enriched)
            elif candidate.get("merged_at") and _iso_to_ms(candidate["merged_at"]) < cutoff_ms:
                # Family counterparts live in a separate git history, so their
                # order comes from the merge time rather than ancestry.
                prior.append(candidate)
        prior.sort(
            key=lambda record: (
                _iso_to_ms(record["merged_at"]) if record.get("merged_at") else 0,
                record["id"],
            )
        )
        return prior

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            [_GIT_BINARY, "-C", self.repo_dir, *args],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            raise ValueError(f"git {' '.join(args)} in {self.repo_dir}: {message}")
        return result.stdout.strip()

    def _is_ancestor(self, commit: str, ancestor: str | None) -> bool:
        result = subprocess.run(
            [_GIT_BINARY, "-C", self.repo_dir, "merge-base", "--is-ancestor", commit, ancestor],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        message = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"git merge-base --is-ancestor {commit} {ancestor}: {message}")


def clear_generated(dest: str | Path, entries: tuple[str, ...]) -> None:
    """Remove exactly the named tool-owned entries from a directory."""
    dest = Path(dest)
    for name in entries:
        path = dest / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def load_pr_index(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load ``corpus/index/merged_prs.json`` keyed by pull request id."""
    try:
        raw = Path(path).read_text()
    except OSError as exc:
        raise ValueError(f"cannot read index {path}: {exc}") from exc
    try:
        records = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"cannot decode index {path}: {exc}") from exc
    if not isinstance(records, list):
        raise ValueError(f"index {path} must be a JSON array of records")
    index: dict[str, dict[str, Any]] = {}
    for position, record in enumerate(records):
        if not isinstance(record, dict) or "repo" not in record or "number" not in record:
            raise ValueError(f"index {path} record {position} lacks repo/number")
        index[f"{record['repo']}#{record['number']}"] = record
    return index


def _session_end_ms(metadata: dict[str, Any] | None) -> int | None:
    if not metadata:
        return None
    end = metadata.get("timestamp", {}).get("end")
    return end if isinstance(end, int) else None


def _iso_to_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


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

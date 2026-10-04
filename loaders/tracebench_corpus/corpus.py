"""Load the TraceBench Peasant PR corpus and materialize per-PR bundles.

The corpus layout is the flat dump written by ``tracebench-sample dump`` or
downloaded by ``tracebench-sample fetch``::

    manifest.json
    metadata.jsonl          one schema.UnifiedMetadata record per session
    pull_requests.jsonl     sampled pull requests with their split
    traces.jsonl            session-to-pull-request mapping
    transcripts/<session_id>.jsonl   schema.TranscriptContent envelopes

The loader is stdlib-only. Loading directly from HuggingFace uses
``huggingface_hub`` when it is installed (``pip install tracebench-corpus[hf]``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DUMP_FILES = ("manifest.json", "metadata.jsonl", "pull_requests.jsonl", "traces.jsonl")


@dataclass(frozen=True)
class Trace:
    """One session-to-pull-request association."""

    pr: str
    session_id: str
    method: str
    relation: str | None = None
    split: str | None = None


@dataclass(frozen=True)
class Bundle:
    """Everything the corpus holds for one pull request."""

    pr: dict[str, Any]
    traces: list[Trace]
    metadata: list[dict[str, Any]]
    transcripts: dict[str, dict[str, Any]]
    missing_sessions: list[str]

    @property
    def pr_id(self) -> str:
        return self.pr["id"]

    def session_ids(self) -> list[str]:
        return [trace.session_id for trace in self.traces]

    def linked_session_ids(self) -> list[str]:
        return [trace.session_id for trace in self.traces if trace.relation in (None, "linked")]

    def turns(self, session_id: str) -> list[dict[str, Any]]:
        """Return the transcript turns for one session."""
        return self.transcripts[session_id].get("turns", [])


class Corpus:
    """A loaded TraceBench dump."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        missing = [name for name in _DUMP_FILES if not (self.root / name).is_file()]
        if missing:
            raise ValueError(
                f"not a TraceBench dump: missing {', '.join(missing)} in {self.root}"
            )
        self.manifest: dict[str, Any] = _read_json(self.root / "manifest.json")
        self.pull_requests: dict[str, dict[str, Any]] = {
            record["id"]: record for record in _read_jsonl(self.root / "pull_requests.jsonl")
        }
        self.traces: list[Trace] = [
            Trace(
                pr=record["pr"],
                session_id=record["session_id"],
                method=record["method"],
                relation=record.get("relation"),
                split=record.get("split"),
            )
            for record in _read_jsonl(self.root / "traces.jsonl")
        ]
        self.metadata: dict[str, dict[str, Any]] = {
            record["sessionId"]: record for record in _read_jsonl(self.root / "metadata.jsonl")
        }
        self._traces_by_pr: dict[str, list[Trace]] = {}
        for trace in self.traces:
            self._traces_by_pr.setdefault(trace.pr, []).append(trace)
        self._envelopes: dict[str, dict[str, Any]] = {}

    # -- queries ---------------------------------------------------------

    def prs(self) -> list[dict[str, Any]]:
        """All sampled pull requests."""
        return list(self.pull_requests.values())

    def splits(self) -> dict[str, list[str]]:
        """Pull request ids grouped by split."""
        grouped: dict[str, list[str]] = {}
        for pr in self.pull_requests.values():
            grouped.setdefault(pr.get("split", "unknown"), []).append(pr["id"])
        return grouped

    def sessions_for_pr(self, pr_id: str) -> list[Trace]:
        """Traces associated with one pull request."""
        return list(self._traces_by_pr.get(pr_id, []))

    def envelope(self, session_id: str) -> dict[str, Any]:
        """The full ``schema.TranscriptContent`` envelope for one session."""
        cached = self._envelopes.get(session_id)
        if cached is not None:
            return cached
        path = self.root / "transcripts" / f"{session_id}.jsonl"
        if not path.is_file():
            raise KeyError(f"transcript {session_id} is not in {self.root / 'transcripts'}")
        for line in path.read_text().splitlines():
            if line.strip():
                envelope = json.loads(line)
                self._envelopes[session_id] = envelope
                return envelope
        raise ValueError(f"transcript {session_id} is empty")

    def transcript(self, session_id: str) -> dict[str, Any]:
        """The ``session_detail`` payload (turns, stats) for one session."""
        envelope = self.envelope(session_id)
        detail = envelope.get("sessionDetail")
        if detail is None:
            raise ValueError(f"transcript {session_id} carries no sessionDetail")
        return detail

    def pr_bundle(self, pr_id: str) -> Bundle:
        """Load the pull request with its traces, metadata, and transcripts."""
        pr = self.pull_requests.get(pr_id)
        if pr is None:
            raise KeyError(f"pull request {pr_id} is not in the corpus")
        traces = self.sessions_for_pr(pr_id)
        metadata: list[dict[str, Any]] = []
        transcripts: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        seen: set[str] = set()
        for trace in traces:
            if trace.session_id in seen:
                continue
            seen.add(trace.session_id)
            record = self.metadata.get(trace.session_id)
            if record is not None:
                metadata.append(record)
            try:
                transcripts[trace.session_id] = self.transcript(trace.session_id)
            except (KeyError, ValueError):
                missing.append(trace.session_id)
        return Bundle(
            pr=pr,
            traces=traces,
            metadata=metadata,
            transcripts=transcripts,
            missing_sessions=missing,
        )

    # -- materialization -------------------------------------------------

    def materialize(self, pr_id: str, dest: str | Path) -> Path:
        """Write a self-contained per-PR bundle directory.

        The layout mirrors the dump so it can be copied into a Harbor
        ``environment/`` or read directly from ``tests/``::

            pr.json
            traces.jsonl
            metadata.jsonl
            bundle.json
            transcripts/<session_id>.jsonl
        """
        bundle = self.pr_bundle(pr_id)
        dest = Path(dest)
        (dest / "transcripts").mkdir(parents=True, exist_ok=True)
        _write_json(dest / "pr.json", bundle.pr)
        _write_jsonl(dest / "traces.jsonl", [_trace_record(trace) for trace in bundle.traces])
        _write_jsonl(dest / "metadata.jsonl", bundle.metadata)
        _write_jsonl(dest / "pull_requests.jsonl", [bundle.pr])
        _write_json(
            dest / "manifest.json",
            {
                "schema_version": 1,
                "source": "bundle",
                "sessions": len(bundle.transcripts),
                "metadata_schema_version": self.manifest.get("metadata_schema_version"),
                "push_contract_version": self.manifest.get("push_contract_version"),
            },
        )
        for session_id in bundle.transcripts:
            source = self.root / "transcripts" / f"{session_id}.jsonl"
            (dest / "transcripts" / f"{session_id}.jsonl").write_bytes(source.read_bytes())
        _write_json(
            dest / "bundle.json",
            {
                "pr": bundle.pr_id,
                "split": bundle.pr.get("split"),
                "traces": len(bundle.traces),
                "sessions": len(bundle.transcripts),
                "missing_sessions": bundle.missing_sessions,
                "corpus": {
                    "metadata_schema_version": self.manifest.get("metadata_schema_version"),
                    "push_contract_version": self.manifest.get("push_contract_version"),
                },
            },
        )
        return dest

    def materialize_all(self, dest: str | Path, split: str | None = None) -> list[Path]:
        """Materialize every pull request (optionally one split)."""
        written: list[Path] = []
        for pr in self.prs():
            if split is not None and pr.get("split") != split:
                continue
            repo_dir = pr["repo"].replace("/", "--")
            target = Path(dest) / repo_dir / f"pr-{pr['number']:04d}"
            written.append(self.materialize(pr["id"], target))
        return written


def load_corpus(
    path: str | Path | None = None,
    repo: str | None = None,
    revision: str = "main",
    cache_dir: str | Path | None = None,
) -> Corpus:
    """Load a local dump (``path``) or a HuggingFace dataset (``repo``)."""
    if path is not None:
        return Corpus(path)
    if repo:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "loading from HuggingFace needs huggingface_hub; "
                "install it with 'pip install tracebench-corpus[hf]'"
            ) from exc
        root = snapshot_download(
            repo_id=repo,
            repo_type="dataset",
            revision=revision,
            cache_dir=str(cache_dir) if cache_dir is not None else None,
            allow_patterns=[
                "manifest.json",
                "metadata.jsonl",
                "pull_requests.jsonl",
                "traces.jsonl",
                "transcripts/*",
            ],
        )
        return Corpus(root)
    raise ValueError("pass path= (a local dump) or repo= (a HuggingFace dataset id)")


def _trace_record(trace: Trace) -> dict[str, Any]:
    record: dict[str, Any] = {"pr": trace.pr, "session_id": trace.session_id, "method": trace.method}
    if trace.relation is not None:
        record["relation"] = trace.relation
    if trace.split is not None:
        record["split"] = trace.split
    return record


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")

"""Shared fixtures: tiny corpus dumps and a git repository for boundary tests."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tracebench_corpus import MAX_BODY_CHARS, load_target_configs

ARCHIVE = "peasant-labs/peasant-prerelease-archive"
LIVE = "peasant-labs/peasant"

TESTDATA = Path(__file__).parent / "testdata"

DEFAULT_MANIFEST = {
    "schema_version": 1,
    "sessions": 5,
    "metadata_schema_version": 11,
    "push_contract_version": "0.1.1",
}


def iso_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def envelope(session_id: str) -> dict:
    return {
        "contractVersion": "0.1.1",
        "kind": "session_detail",
        "sessionDetail": {
            "id": session_id,
            "turnCount": 1,
            "turns": [{"index": 0, "role": "user", "content": session_id}],
        },
    }


def metadata(session_id: str, end: str | None) -> dict:
    record: dict = {"sessionId": session_id, "harness": "claude-code"}
    if end:
        record["timestamp"] = {"start": 0, "end": iso_ms(end)}
    return record


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def standard_records() -> tuple[list[dict], list[dict], list[dict], dict[str, dict]]:
    """The default fixture: two repositories, four pull requests, five sessions.

    No ``created_at`` anywhere: this mirrors the published dump shape.
    """
    pull_requests = [
        {"id": f"{ARCHIVE}#10", "repo": ARCHIVE, "number": 10, "title": "archive work",
         "split": "train", "merged_at": "2026-06-01T00:00:00Z"},
        {"id": f"{LIVE}#20", "repo": LIVE, "number": 20, "title": "earlier live work",
         "split": "train", "merged_at": "2026-08-10T00:00:00Z"},
        {"id": f"{LIVE}#21", "repo": LIVE, "number": 21, "title": "more live work",
         "split": "val", "merged_at": "2026-08-20T00:00:00Z"},
        {"id": f"{LIVE}#22", "repo": LIVE, "number": 22, "title": "the task",
         "split": "test", "merged_at": "2026-09-01T00:00:00Z"},
    ]
    traces = [
        {"pr": f"{ARCHIVE}#10", "session_id": "a1", "method": "exact", "relation": "linked", "split": "train"},
        {"pr": f"{LIVE}#20", "session_id": "l1", "method": "exact", "relation": "linked", "split": "train"},
        # l2 belongs to both #21 and the task PR; it must not appear as prior context.
        {"pr": f"{LIVE}#21", "session_id": "l2", "method": "exact", "relation": "linked", "split": "val"},
        {"pr": f"{LIVE}#22", "session_id": "l2", "method": "exact", "relation": "linked", "split": "test"},
        {"pr": f"{LIVE}#22", "session_id": "own1", "method": "context", "relation": "ancestor", "split": "test"},
    ]
    metadata_records = [
        metadata("a1", "2026-05-25T00:00:00Z"),
        metadata("l1", "2026-08-05T00:00:00Z"),
        metadata("l2", "2026-08-24T00:00:00Z"),
        metadata("own1", "2026-08-30T00:00:00Z"),
    ]
    transcripts = {session_id: envelope(session_id) for session_id in ("a1", "l1", "l2", "own1")}
    return pull_requests, traces, metadata_records, transcripts


@pytest.fixture
def write_dump():
    """Write a raw corpus dump from explicit records."""

    def build(
        root: Path,
        *,
        pull_requests: list[dict],
        traces: list[dict],
        metadata_records: list[dict],
        transcripts: dict[str, dict],
        manifest: dict | None = None,
    ) -> Path:
        root = Path(root)
        (root / "transcripts").mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text(json.dumps(manifest or DEFAULT_MANIFEST))
        write_jsonl(root / "pull_requests.jsonl", pull_requests)
        write_jsonl(root / "traces.jsonl", traces)
        write_jsonl(root / "metadata.jsonl", metadata_records)
        for session_id, payload in transcripts.items():
            (root / "transcripts" / f"{session_id}.jsonl").write_text(json.dumps(payload) + "\n")
        return root

    return build


@pytest.fixture
def standard_dump(tmp_path: Path, write_dump) -> Path:
    pull_requests, traces, metadata_records, transcripts = standard_records()
    return write_dump(
        tmp_path / "dump",
        pull_requests=pull_requests,
        traces=traces,
        metadata_records=metadata_records,
        transcripts=transcripts,
    )


@pytest.fixture
def standard_index() -> dict[str, dict]:
    """Merge commits for the standard fixture; only #22 carries created_at."""
    return {
        f"{ARCHIVE}#10": {"merge_commit": "c10"},
        f"{LIVE}#20": {"merge_commit": "c20"},
        f"{LIVE}#21": {"merge_commit": "c21"},
        f"{LIVE}#22": {"merge_commit": "c22", "created_at": "2026-08-24T00:00:00Z",
                       "base_ref": "develop"},
    }


#: Pull request body for the task pull request (#22) in the body fixtures.
BODY_TEXT = "Fix the greeting endpoint to return 200 with the caller's name."


def _dump_with_body(write_dump, root: Path, body: str | None) -> Path:
    """Write the standard dump with ``body`` on the task pull request (#22).

    ``None`` leaves every record body-free, mirroring the published dump.
    """
    pull_requests, traces, metadata_records, transcripts = standard_records()
    if body is not None:
        for record in pull_requests:
            if record["id"] == f"{LIVE}#22":
                record["body"] = body
    return write_dump(
        root,
        pull_requests=pull_requests,
        traces=traces,
        metadata_records=metadata_records,
        transcripts=transcripts,
    )


@pytest.fixture
def body_dump(tmp_path: Path, write_dump) -> Path:
    """Standard dump whose task pull request (#22) carries a PR body."""
    return _dump_with_body(write_dump, tmp_path / "dump", BODY_TEXT)


@pytest.fixture
def long_body_dump(tmp_path: Path, write_dump) -> Path:
    """Standard dump whose task pull request carries an over-long PR body."""
    return _dump_with_body(write_dump, tmp_path / "dump", "word " * (MAX_BODY_CHARS // 5 + 200))


@pytest.fixture
def target_config_spec():
    """The target-configuration matrix from testdata, loaded by the shared loader."""
    return load_target_configs(TESTDATA / "target_configurations.yaml")


@pytest.fixture
def git_repo(tmp_path: Path):
    """A tiny linear repository; ``commit(name)`` returns the commit SHA."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "tracebench@example.com")
    git("config", "user.name", "TraceBench")

    def commit(name: str) -> str:
        (repo / f"{name}.txt").write_text(name)
        git("add", ".")
        git("-c", "commit.gpgsign=false", "commit", "-q", "-m", name)
        return git("rev-parse", "HEAD")

    return repo, commit

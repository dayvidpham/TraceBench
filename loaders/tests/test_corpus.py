"""Bare-minimum tests for the corpus loader and bundle materializer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracebench_corpus import Corpus, load_corpus
from tracebench_corpus.cli import main


def write_dump(root: Path, *, omit_transcript: str | None = None) -> Path:
    """Write a tiny corpus dump with two pull requests and three sessions."""
    (root / "transcripts").mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sessions": 3,
                "metadata_schema_version": 11,
                "push_contract_version": "0.1.1",
            }
        )
    )
    pull_requests = [
        {
            "id": "peasant-labs/peasant#1",
            "repo": "peasant-labs/peasant",
            "number": 1,
            "title": "feat: one",
            "split": "train",
            "merged_at": "2026-08-10T00:00:00Z",
        },
        {
            "id": "peasant-labs/peasant#2",
            "repo": "peasant-labs/peasant",
            "number": 2,
            "title": "fix: two",
            "split": "val",
            "merged_at": "2026-08-11T00:00:00Z",
        },
    ]
    traces = [
        {"pr": "peasant-labs/peasant#1", "session_id": "session-1", "method": "exact", "relation": "linked", "split": "train"},
        {"pr": "peasant-labs/peasant#1", "session_id": "session-2", "method": "context", "relation": "ancestor", "split": "train"},
        {"pr": "peasant-labs/peasant#2", "session_id": "session-3", "method": "exact", "split": "val"},
    ]
    metadata = [
        {"sessionId": "session-1", "harness": "claude-code", "contentHash": "a"},
        {"sessionId": "session-2", "harness": "claude-code", "contentHash": "b"},
        {"sessionId": "session-3", "harness": "opencode", "contentHash": "c"},
    ]
    _write_jsonl(root / "pull_requests.jsonl", pull_requests)
    _write_jsonl(root / "traces.jsonl", traces)
    _write_jsonl(root / "metadata.jsonl", metadata)
    for index, session_id in enumerate(("session-1", "session-2", "session-3")):
        if session_id == omit_transcript:
            continue
        envelope = {
            "contractVersion": "0.1.1",
            "kind": "session_detail",
            "sessionDetail": {
                "id": session_id,
                "harness": "claude-code",
                "turnCount": 1,
                "turns": [{"index": 0, "role": "user", "content": f"hello {index}"}],
            },
        }
        (root / "transcripts" / f"{session_id}.jsonl").write_text(json.dumps(envelope) + "\n")
    return root


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def test_loads_counts_and_splits(tmp_path: Path) -> None:
    corpus = load_corpus(path=write_dump(tmp_path / "dump"))
    assert isinstance(corpus, Corpus)
    assert len(corpus.prs()) == 2
    assert len(corpus.traces) == 3
    assert len(corpus.metadata) == 3
    assert sorted(corpus.splits()) == ["train", "val"]
    assert corpus.splits()["train"] == ["peasant-labs/peasant#1"]


def test_pr_bundle_joins_traces_metadata_and_turns(tmp_path: Path) -> None:
    corpus = Corpus(write_dump(tmp_path / "dump"))
    bundle = corpus.pr_bundle("peasant-labs/peasant#1")
    assert bundle.pr_id == "peasant-labs/peasant#1"
    assert bundle.session_ids() == ["session-1", "session-2"]
    assert bundle.linked_session_ids() == ["session-1"]
    assert [record["sessionId"] for record in bundle.metadata] == ["session-1", "session-2"]
    assert bundle.turns("session-1")[0]["content"] == "hello 0"
    assert bundle.missing_sessions == []


def test_missing_transcript_is_reported(tmp_path: Path) -> None:
    corpus = Corpus(write_dump(tmp_path / "dump", omit_transcript="session-2"))
    bundle = corpus.pr_bundle("peasant-labs/peasant#1")
    assert bundle.missing_sessions == ["session-2"]
    assert "session-1" in bundle.transcripts


def test_materialize_writes_self_contained_bundle(tmp_path: Path) -> None:
    corpus = Corpus(write_dump(tmp_path / "dump"))
    dest = corpus.materialize("peasant-labs/peasant#2", tmp_path / "out" / "pr-0002")
    assert (dest / "pr.json").is_file()
    assert (dest / "traces.jsonl").is_file()
    assert (dest / "metadata.jsonl").is_file()
    assert (dest / "transcripts" / "session-3.jsonl").is_file()
    bundle = json.loads((dest / "bundle.json").read_text())
    assert bundle["pr"] == "peasant-labs/peasant#2"
    assert bundle["split"] == "val"
    assert bundle["sessions"] == 1
    assert bundle["corpus"]["metadata_schema_version"] == 11

    # A materialized bundle is itself a valid mini corpus.
    mini = Corpus(dest)
    assert [pr["id"] for pr in mini.prs()] == ["peasant-labs/peasant#2"]
    assert mini.pr_bundle("peasant-labs/peasant#2").turns("session-3")[0]["content"] == "hello 2"


def test_materialize_all_groups_by_repo_and_split(tmp_path: Path) -> None:
    corpus = Corpus(write_dump(tmp_path / "dump"))
    written = corpus.materialize_all(tmp_path / "all", split="train")
    assert [path.name for path in written] == ["pr-0001"]
    assert (tmp_path / "all" / "peasant-labs--peasant" / "pr-0001" / "bundle.json").is_file()


def test_errors_for_bad_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Corpus(tmp_path / "missing")
    corpus = Corpus(write_dump(tmp_path / "dump"))
    with pytest.raises(KeyError):
        corpus.pr_bundle("peasant-labs/peasant#999")
    with pytest.raises(KeyError):
        corpus.transcript("session-999")
    with pytest.raises(ValueError):
        load_corpus()


def test_cli_list_bundle_and_bundle_all(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = write_dump(tmp_path / "dump")
    assert main(["--corpus", str(root), "list"]) == 0
    output = capsys.readouterr().out
    assert "peasant-labs/peasant#1" in output
    assert "2 sessions" in output

    assert main(["--corpus", str(root), "list", "--split", "val"]) == 0
    output = capsys.readouterr().out
    assert "peasant-labs/peasant#2" in output
    assert "#1" not in output

    assert main(["--corpus", str(root), "bundle", "peasant-labs/peasant#1", "--dest", str(tmp_path / "b1")]) == 0
    assert "2 traces" in capsys.readouterr().out

    assert main(["--corpus", str(root), "bundle-all", "--dest", str(tmp_path / "all")]) == 0
    assert "2 bundles" in capsys.readouterr().out

    assert main(["--corpus", str(root), "bundle", "nope/nope#1", "--dest", str(tmp_path / "b2")]) == 2
    assert "not in the corpus" in capsys.readouterr().err


def test_cli_requires_a_source(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list"]) == 2
    assert "pass path=" in capsys.readouterr().err

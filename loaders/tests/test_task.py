"""Tests for task payload assembly."""

from __future__ import annotations

import json
from pathlib import Path

from tracebench_corpus import Corpus, TaskBuilder, load_pr_index, repo_family
from tracebench_corpus.cli import main


def write_task_dump(root: Path) -> Path:
    """Two repositories, four pull requests, five sessions."""
    (root / "transcripts").mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sessions": 5,
                "metadata_schema_version": 11,
                "push_contract_version": "0.1.1",
            }
        )
    )
    archive = "peasant-labs/peasant-prerelease-archive"
    live = "peasant-labs/peasant"
    pull_requests = [
        {"id": f"{archive}#10", "repo": archive, "number": 10, "title": "archive work", "split": "train", "merged_at": "2026-06-01T00:00:00Z", "created_at": "2026-05-20T00:00:00Z"},
        {"id": f"{live}#20", "repo": live, "number": 20, "title": "earlier live work", "split": "train", "merged_at": "2026-08-10T00:00:00Z", "created_at": "2026-08-01T00:00:00Z"},
        {"id": f"{live}#21", "repo": live, "number": 21, "title": "more live work", "split": "val", "merged_at": "2026-08-20T00:00:00Z", "created_at": "2026-08-15T00:00:00Z"},
        {"id": f"{live}#22", "repo": live, "number": 22, "title": "the task", "split": "test", "merged_at": "2026-09-01T00:00:00Z", "created_at": "2026-08-25T00:00:00Z"},
    ]
    traces = [
        {"pr": f"{archive}#10", "session_id": "a1", "method": "exact", "relation": "linked", "split": "train"},
        {"pr": f"{live}#20", "session_id": "l1", "method": "exact", "relation": "linked", "split": "train"},
        # l2 belongs to both #21 and the task PR; it must not appear as prior context.
        {"pr": f"{live}#21", "session_id": "l2", "method": "exact", "relation": "linked", "split": "val"},
        {"pr": f"{live}#22", "session_id": "l2", "method": "exact", "relation": "linked", "split": "test"},
        {"pr": f"{live}#22", "session_id": "own1", "method": "context", "relation": "ancestor", "split": "test"},
    ]
    metadata = [{"sessionId": session_id, "harness": "claude-code"} for session_id in ("a1", "l1", "l2", "own1")]
    _write_jsonl(root / "pull_requests.jsonl", pull_requests)
    _write_jsonl(root / "traces.jsonl", traces)
    _write_jsonl(root / "metadata.jsonl", metadata)
    for session_id in ("a1", "l1", "l2", "own1"):
        envelope = {
            "contractVersion": "0.1.1",
            "kind": "session_detail",
            "sessionDetail": {"id": session_id, "turns": [{"index": 0, "role": "user", "content": session_id}]},
        }
        (root / "transcripts" / f"{session_id}.jsonl").write_text(json.dumps(envelope) + "\n")
    return root


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_repo_family_pairs_archive_and_live() -> None:
    assert repo_family("peasant-labs/peasant") == {
        "peasant-labs/peasant",
        "peasant-labs/peasant-prerelease-archive",
    }
    assert repo_family("peasant-labs/peasant-prerelease-archive") == {
        "peasant-labs/peasant",
        "peasant-labs/peasant-prerelease-archive",
    }


def test_task_payload_prior_traces_exclude_own_sessions(tmp_path: Path) -> None:
    corpus = Corpus(write_task_dump(tmp_path / "dump"))
    payload = TaskBuilder(corpus).build("peasant-labs/peasant#22", tmp_path / "task")
    assert payload.prior_pull_requests == 3
    assert payload.prior_sessions == 2
    assert payload.cutoff_time == "2026-08-25T00:00:00Z"

    traces = _read_jsonl(payload.path / "prior-traces" / "traces.jsonl")
    assert sorted({trace["session_id"] for trace in traces}) == ["a1", "l1"]
    # The task PR's own sessions never leak into prior context.
    assert all(trace["session_id"] not in ("l2", "own1") for trace in traces)
    assert sorted({trace["pr"] for trace in traces}) == [
        "peasant-labs/peasant#20",
        "peasant-labs/peasant-prerelease-archive#10",
    ]

    manifest = json.loads((payload.path / "prior-traces" / "manifest.json").read_text())
    assert manifest["cutoff"]["exclusive"] is True
    assert manifest["family"] == [
        "peasant-labs/peasant",
        "peasant-labs/peasant-prerelease-archive",
    ]
    assert manifest["sessions"] == 2
    assert sorted(manifest["excluded_pr_sessions"]) == ["l2", "own1"]

    for session_id in ("a1", "l1"):
        assert (payload.path / "prior-traces" / "transcripts" / f"{session_id}.jsonl").is_file()
    assert not (payload.path / "prior-traces" / "transcripts" / "l2.jsonl").exists()


def test_task_payload_layout_and_integration_point(tmp_path: Path) -> None:
    corpus = Corpus(write_task_dump(tmp_path / "dump"))
    payload = TaskBuilder(corpus).build("peasant-labs/peasant#22", tmp_path / "task")

    assert json.loads((payload.path / "pr.json").read_text())["id"] == "peasant-labs/peasant#22"
    assert (payload.path / "repo").is_dir() and not any((payload.path / "repo").iterdir())
    assert (payload.path / "tests").is_dir() and not any((payload.path / "tests").iterdir())

    request = json.loads((payload.path / "repo-request.json").read_text())
    assert request["pr"] == "peasant-labs/peasant#22"
    assert request["repo"] == "peasant-labs/peasant"
    assert request["number"] == 22
    assert request["merge_commit"] is None
    assert request["base_commit_rule"] == "first parent of merge_commit"
    assert request["cutoff"] == {"kind": "pr", "exclusive": True, "time": "2026-08-25T00:00:00Z"}
    assert set(request["requires"]) == {"repo/", "tests/"}
    assert "**/*_test.go" in request["test_patterns"]

    task = json.loads((payload.path / "task.json").read_text())
    assert task["pr"] == "peasant-labs/peasant#22"
    assert task["prior_pull_requests"] == 3
    assert task["prior_sessions"] == 2


def test_task_payload_enriches_from_pr_index(tmp_path: Path) -> None:
    corpus = Corpus(write_task_dump(tmp_path / "dump"))
    index_path = tmp_path / "merged_prs.json"
    index_path.write_text(
        json.dumps(
            [
                {
                    "repo": "peasant-labs/peasant",
                    "number": 22,
                    "merge_commit": "abc123",
                    "head_oid": "def456",
                    "created_at": "2026-08-24T00:00:00Z",
                    "base_ref": "develop",
                }
            ]
        )
    )
    index = load_pr_index(index_path)
    payload = TaskBuilder(corpus, pr_index=index).build("peasant-labs/peasant#22", tmp_path / "task")

    assert payload.merge_commit == "abc123"
    assert payload.cutoff_time == "2026-08-24T00:00:00Z"
    pr = json.loads((payload.path / "pr.json").read_text())
    assert pr["merge_commit"] == "abc123"
    assert pr["head_oid"] == "def456"
    request = json.loads((payload.path / "repo-request.json").read_text())
    assert request["merge_commit"] == "abc123"
    assert request["cutoff"]["time"] == "2026-08-24T00:00:00Z"


def test_task_cli(tmp_path: Path, capsys) -> None:
    root = write_task_dump(tmp_path / "dump")
    assert main(["--corpus", str(root), "task", "peasant-labs/peasant#22", "--dest", str(tmp_path / "t")]) == 0
    output = capsys.readouterr().out
    assert "2 prior traces from 3 pull requests (2 sessions)" in output
    assert "repo/ and tests/ await the repository tooling" in output
    assert main(["--corpus", str(root), "task", "nope/nope#1", "--dest", str(tmp_path / "t2")]) == 2
    assert "not in the corpus" in capsys.readouterr().err

"""Bare-minimum tests for the corpus loader and bundle materializer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import ARCHIVE, LIVE, standard_records
from tracebench_corpus import Corpus, load_corpus
from tracebench_corpus.cli import main


def test_loads_counts_and_splits(standard_dump) -> None:
    corpus = load_corpus(path=standard_dump)
    assert isinstance(corpus, Corpus)
    assert len(corpus.prs()) == 4
    assert len(corpus.traces) == 5
    assert len(corpus.metadata) == 4
    assert sorted(corpus.splits()) == ["test", "train", "val"]
    assert corpus.splits()["train"] == [f"{ARCHIVE}#10", f"{LIVE}#20"]


def test_pr_bundle_joins_traces_metadata_and_turns(standard_dump) -> None:
    corpus = Corpus(standard_dump)
    bundle = corpus.pr_bundle(f"{ARCHIVE}#10")
    assert bundle.pr_id == f"{ARCHIVE}#10"
    assert bundle.session_ids() == ["a1"]
    assert [record["sessionId"] for record in bundle.metadata] == ["a1"]
    assert bundle.turns("a1")[0]["content"] == "a1"
    assert bundle.missing_sessions == []


def test_missing_transcript_is_reported(write_dump, tmp_path) -> None:
    pull_requests, traces, metadata_records, transcripts = standard_records()
    transcripts.pop("l2")
    root = write_dump(
        tmp_path / "dump",
        pull_requests=pull_requests,
        traces=traces,
        metadata_records=metadata_records,
        transcripts=transcripts,
    )
    bundle = Corpus(root).pr_bundle(f"{LIVE}#21")
    assert bundle.missing_sessions == ["l2"]
    assert "l2" not in bundle.transcripts


def test_materialize_writes_self_contained_bundle(standard_dump, tmp_path) -> None:
    corpus = Corpus(standard_dump)
    dest = corpus.materialize(f"{ARCHIVE}#10", tmp_path / "out" / "pr-0010")
    assert (dest / "pr.json").is_file()
    assert (dest / "traces.jsonl").is_file()
    assert (dest / "metadata.jsonl").is_file()
    assert (dest / "transcripts" / "a1.jsonl").is_file()
    bundle = json.loads((dest / "bundle.json").read_text())
    assert bundle["pr"] == f"{ARCHIVE}#10"
    assert bundle["split"] == "train"
    assert bundle["sessions"] == 1
    assert bundle["corpus"]["metadata_schema_version"] == 11

    # A materialized bundle is itself a valid mini corpus.
    mini = Corpus(dest)
    assert [pr["id"] for pr in mini.prs()] == [f"{ARCHIVE}#10"]
    assert mini.pr_bundle(f"{ARCHIVE}#10").turns("a1")[0]["content"] == "a1"


def test_materialize_all_groups_by_repo_and_split(standard_dump, tmp_path) -> None:
    corpus = Corpus(standard_dump)
    written = corpus.materialize_all(tmp_path / "all", split="train")
    assert sorted(path.name for path in written) == ["pr-0010", "pr-0020"]
    assert (tmp_path / "all" / "peasant-labs--peasant" / "pr-0020" / "bundle.json").is_file()


def test_errors_for_bad_inputs(standard_dump, tmp_path) -> None:
    with pytest.raises(ValueError):
        Corpus(tmp_path / "missing")
    corpus = Corpus(standard_dump)
    with pytest.raises(KeyError):
        corpus.pr_bundle(f"{LIVE}#999")
    with pytest.raises(KeyError):
        corpus.transcript("session-999")
    with pytest.raises(ValueError):
        load_corpus()


def test_cli_list_bundle_and_bundle_all(standard_dump, tmp_path, capsys) -> None:
    assert main(["--corpus", str(standard_dump), "list"]) == 0
    output = capsys.readouterr().out
    assert f"{ARCHIVE}#10" in output

    assert main(["--corpus", str(standard_dump), "list", "--split", "val"]) == 0
    output = capsys.readouterr().out
    assert f"{LIVE}#21" in output
    assert f"{ARCHIVE}#10" not in output

    assert main(["--corpus", str(standard_dump), "bundle", f"{ARCHIVE}#10", "--dest", str(tmp_path / "b1")]) == 0
    assert "1 traces" in capsys.readouterr().out

    assert main(["--corpus", str(standard_dump), "bundle-all", "--dest", str(tmp_path / "all")]) == 0
    assert "4 bundles" in capsys.readouterr().out

    assert main(["--corpus", str(standard_dump), "bundle", "nope/nope#1", "--dest", str(tmp_path / "b2")]) == 2
    assert "not in the corpus" in capsys.readouterr().err


def test_cli_requires_a_source(capsys) -> None:
    assert main(["list"]) == 2
    assert "pass path=" in capsys.readouterr().err


def test_pr_body_round_trips_when_present(write_dump, tmp_path) -> None:
    from conftest import standard_records

    pull_requests, traces, metadata_records, transcripts = standard_records()
    pull_requests[0]["body"] = "Intent text for the change."
    root = write_dump(
        tmp_path / "dump",
        pull_requests=pull_requests,
        traces=traces,
        metadata_records=metadata_records,
        transcripts=transcripts,
    )
    corpus = Corpus(root)
    assert corpus.pull_requests[f"{ARCHIVE}#10"]["body"] == "Intent text for the change."
    bundle = corpus.pr_bundle(f"{ARCHIVE}#10")
    assert bundle.pr["body"] == "Intent text for the change."

    dest = corpus.materialize(f"{ARCHIVE}#10", tmp_path / "out" / "pr-0010")
    mini = Corpus(dest)
    assert mini.pull_requests[f"{ARCHIVE}#10"]["body"] == "Intent text for the change."


def test_pr_body_absent_loads_as_null(standard_dump) -> None:
    corpus = Corpus(standard_dump)
    assert corpus.prs(), "expected the standard dump to carry pull requests"
    for pr in corpus.prs():
        assert pr.get("body") is None
    bundle = corpus.pr_bundle(f"{ARCHIVE}#10")
    assert bundle.pr.get("body") is None


def test_linked_issue_round_trips_when_present(write_dump, tmp_path) -> None:
    from conftest import standard_records

    pull_requests, traces, metadata_records, transcripts = standard_records()
    pull_requests[0]["issue"] = {"number": 10, "title": "the issue",
                                 "body": "Issue intent text.", "url": "u"}
    root = write_dump(
        tmp_path / "dump",
        pull_requests=pull_requests,
        traces=traces,
        metadata_records=metadata_records,
        transcripts=transcripts,
    )
    corpus = Corpus(root)
    assert corpus.pull_requests[f"{ARCHIVE}#10"]["issue"]["body"] == "Issue intent text."
    bundle = corpus.pr_bundle(f"{ARCHIVE}#10")
    assert bundle.pr["issue"]["body"] == "Issue intent text."

    dest = corpus.materialize(f"{ARCHIVE}#10", tmp_path / "out" / "pr-0010")
    mini = Corpus(dest)
    assert mini.pull_requests[f"{ARCHIVE}#10"]["issue"]["body"] == "Issue intent text."

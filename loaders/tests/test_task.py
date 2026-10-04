"""Tests for task payload assembly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import ARCHIVE, LIVE, TESTDATA, envelope, metadata
from tracebench_corpus import (
    Corpus,
    TaskBuilder,
    find_target_config,
    load_pr_index,
    load_target_configs,
    repo_family,
)
from tracebench_corpus.cli import main


def write_index(tmp_path: Path, index: dict[str, dict]) -> Path:
    records = []
    for pr_id, record in index.items():
        repo, number = pr_id.rsplit("#", 1)
        records.append({"repo": repo, "number": int(number), **record})
    path = tmp_path / "merged_prs.json"
    path.write_text(json.dumps(records))
    return path


def test_repo_family_pairs_archive_and_live() -> None:
    assert repo_family(LIVE) == {LIVE, ARCHIVE}
    assert repo_family(ARCHIVE) == {LIVE, ARCHIVE}


def test_task_payload_prior_traces_exclude_own_sessions(standard_dump, standard_index, tmp_path) -> None:
    corpus = Corpus(standard_dump)
    payload = TaskBuilder(corpus, pr_index=standard_index).build(f"{LIVE}#22", tmp_path / "task")
    assert payload.prior_pull_requests == 3
    assert payload.prior_sessions == 2
    assert payload.cutoff_basis == "merged_at"
    assert payload.cutoff_time == "2026-09-01T00:00:00Z"
    assert payload.boundary_commit is None
    assert payload.merge_commit == "c22"
    assert payload.sessions_past_cutoff == 0
    assert payload.missing_sessions == 0

    traces = [json.loads(line) for line in (payload.path / "prior-traces" / "traces.jsonl").read_text().splitlines()]
    assert sorted({trace["session_id"] for trace in traces}) == ["a1", "l1"]
    assert all(trace["session_id"] not in ("l2", "own1") for trace in traces)
    assert {trace["pr"] for trace in traces} == {f"{ARCHIVE}#10", f"{LIVE}#20"}

    manifest = json.loads((payload.path / "prior-traces" / "manifest.json").read_text())
    assert manifest["cutoff"]["basis"] == "merged_at"
    assert manifest["cutoff"]["exclusive"] is True
    assert manifest["family"] == [LIVE, ARCHIVE]
    assert manifest["sessions"] == 2
    assert manifest["materialized_sessions"] == 2
    assert sorted(manifest["excluded_pr_sessions"]) == ["l2", "own1"]
    assert manifest["sessions_past_cutoff"] == []
    assert manifest["missing_sessions"] == []
    assert "sampled" in manifest["sampled_scope"]
    assert (payload.path / "prior-traces" / "transcripts" / "a1.jsonl").is_file()
    assert not (payload.path / "prior-traces" / "transcripts" / "l2.jsonl").exists()


def test_task_target_from_index_without_sampled_sessions(standard_dump, standard_index, tmp_path) -> None:
    corpus = Corpus(standard_dump)
    index = dict(standard_index)
    index[f"{LIVE}#23"] = {"repo": LIVE, "number": 23, "merge_commit": "c23",
                           "merged_at": "2026-09-05T00:00:00Z"}
    payload = TaskBuilder(corpus, pr_index=index).build(f"{LIVE}#23", tmp_path / "task")

    pr = json.loads((payload.path / "pr.json").read_text())
    assert pr["id"] == f"{LIVE}#23"
    assert pr["repo"] == LIVE
    assert pr["number"] == 23
    assert pr["sampled"] is False
    # The target itself is not part of the sampled corpus...
    assert f"{LIVE}#23" not in corpus.pull_requests
    # ...but prior context still comes from it.
    assert payload.prior_pull_requests == 4
    assert payload.prior_sessions == 4
    traces = [json.loads(line) for line in (payload.path / "prior-traces" / "traces.jsonl").read_text().splitlines()]
    assert sorted({trace["session_id"] for trace in traces}) == ["a1", "l1", "l2", "own1"]
    task = json.loads((payload.path / "task.json").read_text())
    assert task["split"] is None
    assert json.loads((payload.path / "repo-request.json").read_text())["merge_commit"] == "c23"


def test_task_target_absent_from_corpus_and_index_fails_closed(standard_dump, standard_index, tmp_path) -> None:
    corpus = Corpus(standard_dump)
    with pytest.raises(KeyError, match="not in the corpus or the index"):
        TaskBuilder(corpus, pr_index=standard_index).build(f"{LIVE}#99", tmp_path / "task")


def test_task_payload_layout_and_integration_point(standard_dump, standard_index, tmp_path) -> None:
    corpus = Corpus(standard_dump)
    payload = TaskBuilder(corpus, pr_index=standard_index).build(f"{LIVE}#22", tmp_path / "task")

    assert json.loads((payload.path / "pr.json").read_text())["id"] == f"{LIVE}#22"
    assert (payload.path / "repo").is_dir() and not any((payload.path / "repo").iterdir())
    assert (payload.path / "tests").is_dir() and not any((payload.path / "tests").iterdir())

    request = json.loads((payload.path / "repo-request.json").read_text())
    assert request["pr"] == f"{LIVE}#22"
    assert request["merge_commit"] == "c22"
    assert request["tree_commit"] is None
    assert request["tree_commit_rule"] == "first parent of merge_commit (the develop commit before the PR)"
    assert request["trace_cutoff"]["basis"] == "merged_at"
    assert request["trace_cutoff"]["time"] == "2026-09-01T00:00:00Z"
    assert request["glob_dialect"] == "doublestar globs relative to the repository root"
    assert "materialize_worktree" in request["note"]
    assert "tree_commit^{tree}" in request["note"]
    assert "merge_commit" in request["note"]
    assert "Adapter required" not in request["note"]
    assert "**/*_test.go" in request["test_patterns"]

    task = json.loads((payload.path / "task.json").read_text())
    assert task["pr"] == f"{LIVE}#22"
    assert task["prior_pull_requests"] == 3
    assert task["prior_sessions"] == 2
    assert task["cutoff"]["basis"] == "merged_at"


def test_index_created_at_does_not_change_the_cutoff(standard_dump, standard_index, tmp_path) -> None:
    # The index carries created_at (2026-08-24) for #22; the ruled cutoff is the
    # develop boundary, so the merged_at fallback must win.
    corpus = Corpus(standard_dump)
    payload = TaskBuilder(corpus, pr_index=standard_index).build(f"{LIVE}#22", tmp_path / "task")
    assert payload.cutoff_time == "2026-09-01T00:00:00Z"
    assert payload.prior_pull_requests == 3


def test_task_requires_a_merge_commit(standard_dump, tmp_path) -> None:
    corpus = Corpus(standard_dump)
    with pytest.raises(ValueError, match="--index"):
        TaskBuilder(corpus).build(f"{LIVE}#22", tmp_path / "task")


def test_destination_reuse_is_rejected_and_force_rebuilds(standard_dump, standard_index, tmp_path) -> None:
    corpus = Corpus(standard_dump)
    dest = tmp_path / "task"
    builder = TaskBuilder(corpus, pr_index=standard_index)
    builder.build(f"{LIVE}#22", dest)
    (dest / "test-manifest.json").write_text('{"stale": true}\n')

    with pytest.raises(ValueError, match="not empty"):
        builder.build(f"{LIVE}#20", dest)

    rebuilt = builder.build(f"{LIVE}#20", dest, force=True)
    manifest = json.loads((dest / "prior-traces" / "manifest.json").read_text())
    files = {path.stem for path in (dest / "prior-traces" / "transcripts").glob("*.jsonl")}
    assert rebuilt.pr_id == f"{LIVE}#20"
    assert files == set(["a1"])
    assert manifest["sessions"] == 1
    assert files == {"a1"}
    assert "l1" not in files  # #20's own session never leaks from the earlier build
    assert not (dest / "test-manifest.json").exists()


def test_sessions_past_the_boundary_are_excluded(standard_index, tmp_path, write_dump) -> None:
    pull_requests, traces, metadata_records, transcripts = standard_records_for_past_cutoff()
    root = write_dump(
        tmp_path / "dump",
        pull_requests=pull_requests,
        traces=traces,
        metadata_records=metadata_records,
        transcripts=transcripts,
    )
    corpus = Corpus(root)
    payload = TaskBuilder(corpus, pr_index=standard_index).build(f"{LIVE}#22", tmp_path / "task")
    assert payload.sessions_past_cutoff == 1
    manifest = json.loads((payload.path / "prior-traces" / "manifest.json").read_text())
    assert manifest["sessions_past_cutoff"] == ["l1"]
    session_ids = {trace["session_id"] for trace in
                   (json.loads(line) for line in (payload.path / "prior-traces" / "traces.jsonl").read_text().splitlines())}
    assert "l1" not in session_ids
    assert not (payload.path / "prior-traces" / "transcripts" / "l1.jsonl").exists()


def standard_records_for_past_cutoff():
    from conftest import standard_records

    pull_requests, traces, _, transcripts = standard_records()
    metadata_records = [
        metadata("a1", "2026-05-25T00:00:00Z"),
        metadata("l1", "2026-09-10T00:00:00Z"),  # runs past the 2026-09-01 boundary
        metadata("l2", "2026-08-24T00:00:00Z"),
        metadata("own1", "2026-08-30T00:00:00Z"),
    ]
    return pull_requests, traces, metadata_records, transcripts


def test_missing_transcripts_are_recorded(standard_index, tmp_path, write_dump) -> None:
    pull_requests, traces, metadata_records, transcripts = _standard_records()
    transcripts.pop("a1")
    root = write_dump(
        tmp_path / "dump",
        pull_requests=pull_requests,
        traces=traces,
        metadata_records=metadata_records,
        transcripts=transcripts,
    )
    payload = TaskBuilder(Corpus(root), pr_index=standard_index).build(f"{LIVE}#22", tmp_path / "task")
    assert payload.missing_sessions == 1
    manifest = json.loads((payload.path / "prior-traces" / "manifest.json").read_text())
    assert manifest["missing_sessions"] == ["a1"]
    assert manifest["sessions"] == 2
    assert manifest["materialized_sessions"] == 1
    assert not (payload.path / "prior-traces" / "transcripts" / "a1.jsonl").exists()


def test_multi_pr_sessions_emit_one_row_per_association(standard_index, tmp_path, write_dump) -> None:
    pull_requests, traces, metadata_records, transcripts = _standard_records()
    # l1 is traced to both #20 and #21 (a production-common shape).
    traces.insert(2, {"pr": f"{LIVE}#21", "session_id": "l1", "method": "commit", "split": "val"})
    root = write_dump(
        tmp_path / "dump",
        pull_requests=pull_requests,
        traces=traces,
        metadata_records=metadata_records,
        transcripts=transcripts,
    )
    payload = TaskBuilder(Corpus(root), pr_index=standard_index).build(f"{LIVE}#22", tmp_path / "task")
    rows = [json.loads(line) for line in (payload.path / "prior-traces" / "traces.jsonl").read_text().splitlines()]
    assert sum(1 for row in rows if row["session_id"] == "l1") == 2
    manifest = json.loads((payload.path / "prior-traces" / "manifest.json").read_text())
    assert manifest["traces"] == 3
    assert manifest["sessions"] == 2
    metadata_rows = [json.loads(line) for line in (payload.path / "prior-traces" / "metadata.jsonl").read_text().splitlines()]
    assert sum(1 for row in metadata_rows if row["sessionId"] == "l1") == 1
    assert (payload.path / "prior-traces" / "transcripts" / "l1.jsonl").is_file()


def test_non_family_candidates_are_excluded(standard_index, tmp_path, write_dump) -> None:
    pull_requests, traces, metadata_records, transcripts = _standard_records()
    pull_requests.append({"id": "other/repo#1", "repo": "other/repo", "number": 1,
                          "title": "elsewhere", "split": "train", "merged_at": "2026-07-01T00:00:00Z"})
    traces.append({"pr": "other/repo#1", "session_id": "o1", "method": "exact", "split": "train"})
    metadata_records.append(metadata("o1", "2026-06-20T00:00:00Z"))
    transcripts["o1"] = envelope("o1")
    root = write_dump(
        tmp_path / "dump",
        pull_requests=pull_requests,
        traces=traces,
        metadata_records=metadata_records,
        transcripts=transcripts,
    )
    payload = TaskBuilder(Corpus(root), pr_index=standard_index).build(f"{LIVE}#22", tmp_path / "task")
    manifest = json.loads((payload.path / "prior-traces" / "manifest.json").read_text())
    assert "other/repo#1" not in manifest["pull_requests"]
    assert not (payload.path / "prior-traces" / "transcripts" / "o1.jsonl").exists()


def test_empty_prior_context(standard_index, tmp_path, write_dump) -> None:
    pull_requests, traces, metadata_records, transcripts = _standard_records()
    pull_requests = [pr for pr in pull_requests if pr["id"] == f"{LIVE}#22"]
    traces = [trace for trace in traces if trace["pr"] == f"{LIVE}#22"]
    root = write_dump(
        tmp_path / "dump",
        pull_requests=pull_requests,
        traces=traces,
        metadata_records=metadata_records,
        transcripts=transcripts,
    )
    payload = TaskBuilder(Corpus(root), pr_index=standard_index).build(f"{LIVE}#22", tmp_path / "task")
    assert payload.prior_traces == 0 and payload.prior_sessions == 0
    assert (payload.path / "prior-traces" / "traces.jsonl").read_text() == ""
    assert (payload.path / "prior-traces" / "metadata.jsonl").read_text() == ""
    manifest = json.loads((payload.path / "prior-traces" / "manifest.json").read_text())
    assert manifest["pull_requests"] == []
    assert manifest["traces"] == 0


def test_ancestry_mode_uses_commit_order(git_repo, tmp_path, write_dump) -> None:
    repo, commit = git_repo
    c10 = commit("c10")
    c20 = commit("c20")
    # A side-branch commit recorded with an earlier merged_at than the target,
    # but not an ancestor of the target's develop parent.
    import subprocess

    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side", c20], check=True)
    c23 = commit("c23")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-"], check=True)
    c21 = commit("c21")
    c22 = commit("c22")

    pull_requests = [
        {"id": f"{ARCHIVE}#10", "repo": ARCHIVE, "number": 10, "title": "a", "merged_at": "2026-06-01T00:00:00Z"},
        {"id": f"{LIVE}#20", "repo": LIVE, "number": 20, "title": "b", "merged_at": "2026-08-10T00:00:00Z"},
        {"id": f"{LIVE}#21", "repo": LIVE, "number": 21, "title": "c", "merged_at": "2026-08-20T00:00:00Z"},
        {"id": f"{LIVE}#23", "repo": LIVE, "number": 23, "title": "side", "merged_at": "2026-08-31T00:00:00Z"},
        {"id": f"{LIVE}#22", "repo": LIVE, "number": 22, "title": "target", "merged_at": "2026-09-01T00:00:00Z"},
    ]
    traces = [
        {"pr": f"{ARCHIVE}#10", "session_id": "s10", "method": "exact", "split": "train"},
        {"pr": f"{LIVE}#20", "session_id": "s20", "method": "exact", "split": "train"},
        {"pr": f"{LIVE}#21", "session_id": "s21", "method": "exact", "split": "val"},
        {"pr": f"{LIVE}#23", "session_id": "s23", "method": "exact", "split": "val"},
        {"pr": f"{LIVE}#22", "session_id": "own", "method": "exact", "split": "test"},
    ]
    metadata_records = [metadata(sid, "2026-05-01T00:00:00Z") for sid in ("s10", "s20", "s21", "s23", "own")]
    transcripts = {sid: envelope(sid) for sid in ("s10", "s20", "s21", "s23", "own")}
    root = write_dump(
        tmp_path / "dump",
        pull_requests=pull_requests,
        traces=traces,
        metadata_records=metadata_records,
        transcripts=transcripts,
    )
    index = {
        f"{ARCHIVE}#10": {"merge_commit": c10},
        f"{LIVE}#20": {"merge_commit": c20},
        f"{LIVE}#21": {"merge_commit": c21},
        f"{LIVE}#23": {"merge_commit": c23},
        f"{LIVE}#22": {"merge_commit": c22},
    }
    corpus = Corpus(root)
    payload = TaskBuilder(corpus, pr_index=index, repo_dir=repo).build(f"{LIVE}#22", tmp_path / "ancestry")
    assert payload.cutoff_basis == "commit_ancestry"
    assert payload.boundary_commit == c21
    assert payload.prior_pull_requests == 3  # #23 is not an ancestor of c21
    test_manifest = json.loads((payload.path / "test-manifest.json").read_text())
    assert test_manifest["base_commit"] == c21
    assert test_manifest["merge_commit"] == c22
    assert test_manifest["suites"] == []
    assert json.loads((payload.path / "task.json").read_text())["test_manifest"] == "test-manifest.json"
    sessions = {json.loads(line)["session_id"] for line in
                (payload.path / "prior-traces" / "traces.jsonl").read_text().splitlines()}
    assert "s23" not in sessions

    fallback = TaskBuilder(corpus, pr_index=index).build(f"{LIVE}#22", tmp_path / "merged-at")
    assert fallback.cutoff_basis == "merged_at"
    assert fallback.prior_pull_requests == 4  # merged_at includes #23


def test_task_cli(standard_dump, standard_index, tmp_path, capsys) -> None:
    index_path = write_index(tmp_path, standard_index)
    assert main([
        "--corpus", str(standard_dump), "task", f"{LIVE}#22",
        "--index", str(index_path), "--dest", str(tmp_path / "cli-task"),
    ]) == 0
    output = capsys.readouterr().out
    assert "cutoff basis merged_at" in output
    assert "3 pull requests (2 sessions" in output

    assert main([
        "--corpus", str(standard_dump), "task", f"{LIVE}#22",
        "--dest", str(tmp_path / "no-index"),
    ]) == 2
    assert "--index" in capsys.readouterr().err

    assert main([
        "--corpus", str(standard_dump), "task", f"{LIVE}#22",
        "--index", str(tmp_path / "missing.json"), "--dest", str(tmp_path / "missing-index"),
    ]) == 2
    assert "cannot read index" in capsys.readouterr().err

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json")
    assert main([
        "--corpus", str(standard_dump), "task", f"{LIVE}#22",
        "--index", str(malformed), "--dest", str(tmp_path / "bad-index"),
    ]) == 2
    assert "cannot decode index" in capsys.readouterr().err


def test_load_pr_index_validation(tmp_path) -> None:
    with pytest.raises(ValueError, match="cannot read index"):
        load_pr_index(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("[{\"repo\": \"x\"}]")
    with pytest.raises(ValueError, match="lacks repo/number"):
        load_pr_index(bad)


def _standard_records():
    from conftest import standard_records

    return standard_records()


TARGET_CONFIG_NAMES = [
    configuration.name
    for configuration in load_target_configs(TESTDATA / "target_configurations.yaml")
]


@pytest.mark.parametrize("config_name", TARGET_CONFIG_NAMES)
def test_target_configuration_is_recorded_and_does_not_filter(
    config_name, target_config_spec, standard_dump, standard_index, tmp_path
) -> None:
    configuration = find_target_config(target_config_spec, config_name)
    payload = TaskBuilder(Corpus(standard_dump), pr_index=standard_index).build(
        f"{LIVE}#22", tmp_path / "task", target_config=configuration
    )
    sessions = {
        json.loads(line)["session_id"]
        for line in (payload.path / "prior-traces" / "traces.jsonl").read_text().splitlines()
    }
    # The configuration never removes prior context: every prior session stays.
    assert sessions == {"a1", "l1"}
    assert payload.target_config == config_name

    manifest = json.loads((payload.path / "prior-traces" / "manifest.json").read_text())
    assert manifest["target_configuration"] == configuration.to_dict()
    assert "excluded_by_adaptation" not in manifest
    task = json.loads((payload.path / "task.json").read_text())
    assert task["target_configuration"]["name"] == config_name


def test_task_cli_target_configuration(standard_dump, standard_index, tmp_path, capsys) -> None:
    index_path = write_index(tmp_path, standard_index)
    assert main([
        "--corpus", str(standard_dump), "task", f"{LIVE}#22",
        "--index", str(index_path),
        "--target-configs", str(TESTDATA / "target_configurations.yaml"),
        "--target-config", "opencode-gpt-medium",
        "--dest", str(tmp_path / "configured"),
    ]) == 0
    output = capsys.readouterr().out
    assert "target configuration: opencode-gpt-medium" in output

    assert main([
        "--corpus", str(standard_dump), "task", f"{LIVE}#22",
        "--index", str(index_path),
        "--target-config", "opencode-gpt-medium",
        "--dest", str(tmp_path / "half"),
    ]) == 2
    assert "must be used together" in capsys.readouterr().err

    assert main([
        "--corpus", str(standard_dump), "task", f"{LIVE}#22",
        "--index", str(index_path),
        "--target-configs", str(TESTDATA / "target_configurations.yaml"),
        "--target-config", "nope",
        "--dest", str(tmp_path / "unknown"),
    ]) == 2
    assert "unknown target configuration" in capsys.readouterr().err

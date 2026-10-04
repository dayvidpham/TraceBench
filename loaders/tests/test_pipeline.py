"""The pipeline driver: PR list -> runnable Harbor tasks + a Harbor job config."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path

import pytest
import yaml
from conftest import LIVE, TESTDATA, envelope, metadata

from tracebench_corpus import Corpus
from tracebench_corpus import pipeline as pipeline_module
from tracebench_corpus.cli import main
from tracebench_corpus.pipeline import (
    N_ATTEMPTS,
    REQUIRED_TASK_PARTS,
    RUN_ID_ENV,
    PipelineError,
    new_run_id,
    read_pr_list,
    run_pipeline,
    task_set_revision,
)
from tracebench_corpus.repository_spec import DEFAULT_REPOSITORY_SPECS
from tracebench_corpus.target_config import TargetConfiguration

FIXTURE = yaml.safe_load((TESTDATA / "pipeline.yaml").read_text())


@pytest.fixture
def pipeline_env(git_repo, tmp_path, write_dump) -> dict:
    """A repository whose commits are the fixture PRs' merges, plus a dump and an index."""
    repo, _commit = git_repo

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    merges: dict[int, str] = {}
    named: dict[str, str] = {}
    for spec in FIXTURE["commits"]:
        for path, content in spec["files"].items():
            (repo / path).write_text(content)
        git("add", ".")
        git("-c", "commit.gpgsign=false", "commit", "-q", "-m", spec["name"])
        named[spec["name"]] = git("rev-parse", "HEAD")
        if "pr" in spec:
            merges[spec["pr"]] = named[spec["name"]]
    for spec in FIXTURE["side_commits"]:
        entries = []
        for path, content in spec["files"].items():
            blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], input=content,
                                  check=True, capture_output=True, text=True).stdout.strip()
            entries.append(f"100644 blob {blob}\t{path}\n")
        tree = subprocess.run(["git", "-C", str(repo), "mktree"], input="".join(entries),
                              check=True, capture_output=True, text=True).stdout.strip()
        merges[spec["pr"]] = git("commit-tree", tree, "-p", named[spec["parent"]], "-m", f"pr{spec['pr']}")

    numbers = (20, 21, 22)
    pull_requests = [
        {"id": f"{LIVE}#{n}", "repo": LIVE, "number": n, "title": f"pr {n}",
         "split": "test", "merged_at": f"2026-09-{n - 10:02d}T00:00:00Z"}
        for n in numbers
    ]
    traces = [{"pr": f"{LIVE}#20", "session_id": "s20", "method": "exact", "split": "test"}]
    dump = write_dump(
        tmp_path / "dump", pull_requests=pull_requests, traces=traces,
        metadata_records=[metadata("s20", "2026-05-01T00:00:00Z")],
        transcripts={"s20": envelope("s20")},
    )
    # PR 23 is index-only: it has no sampled sessions, so the dump omits it.
    index = [{"repo": LIVE, "number": n, "merge_commit": merges[n], "base_ref": "develop"}
             for n in (*numbers, 23)]
    index_path = tmp_path / "merged_prs.json"
    index_path.write_text(json.dumps(index))
    return {"repo": repo, "dump": dump, "index": index_path, "merges": merges}


def _cli(env: dict, dest: Path, *extra: str) -> int:
    return main([
        "--corpus", str(env["dump"]), "pipeline",
        "--repo-dir", str(env["repo"]), "--index", str(env["index"]), "--dest", str(dest),
        *extra,
    ])


def test_two_prs_build_two_runnable_tasks(pipeline_env, tmp_path, capsys) -> None:
    pr_file = tmp_path / "prs.txt"
    pr_file.write_text(f"# the task set\n{LIVE}#20\n\n")
    dest = tmp_path / "out"
    assert _cli(pipeline_env, dest, "--prs", str(pr_file), "--prs", f"{LIVE}#22",
                "--run-id", "run-x") == 0
    out = capsys.readouterr().out
    assert set(REQUIRED_TASK_PARTS) == set(FIXTURE["required_parts"])
    for pr_id, name in FIXTURE["expected_tasks"].items():
        task = dest / "tasks" / name
        assert f"ok     {pr_id}" in out
        for part in FIXTURE["required_parts"]:
            assert (task / part).exists(), f"{name} lacks {part}"
        repo_git = task / "environment" / "repo"
        count = subprocess.run(["git", "-C", str(repo_git), "rev-list", "--all", "--count"],
                               capture_output=True, text=True, check=True).stdout.strip()
        assert int(count) > 1, name
        assert not (repo_git / ".git" / "shallow").exists(), name
        assert subprocess.run(["git", "-C", str(repo_git), "remote"],
                              capture_output=True, text=True, check=True).stdout.strip() == "", name
        branch = subprocess.run(["git", "-C", str(repo_git), "symbolic-ref", "--short", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        assert branch == "develop", name
        assert (task / "tests" / "golden" / "greet_test.go").is_file()
    for name, sessions in FIXTURE["prior_traces"].items():
        transcripts = dest / "tasks" / name / "environment" / "prior-traces" / "transcripts"
        assert sorted(p.stem for p in transcripts.glob("*.jsonl")) == sessions, name
    assert "s20" in (dest / "tasks" / "peasant-pr-0022" / "environment" / "prior-traces"
                     / "transcripts" / "s20.jsonl").read_text()
    # The worktree is the pre-PR tree: #22's repo has #20's greet.go, not #22's.
    repo22 = dest / "tasks" / "peasant-pr-0022" / "environment" / "repo"
    assert "hi" in (repo22 / "greet.go").read_text()
    assert "hello" in (dest / "tasks" / "peasant-pr-0022" / "tests" / "golden" / "greet_test.go").read_text()
    patch = (dest / "tasks" / "peasant-pr-0022" / "solution" / "oracle.patch").read_text()
    assert "greet.go" in patch


def test_index_only_pr_builds_a_task(pipeline_env, tmp_path, capsys) -> None:
    dest = tmp_path / "out"
    assert _cli(pipeline_env, dest, "--prs", f"{LIVE}#23", "--run-id", "run-23") == 0
    out = capsys.readouterr().out
    assert f"ok     {LIVE}#23" in out
    task = dest / "tasks" / "peasant-pr-0023"
    for part in FIXTURE["required_parts"]:
        assert (task / part).exists(), f"peasant-pr-0023 lacks {part}"
    # Prior context still comes from the sampled corpus: s20 is linked to PR 20.
    transcripts = task / "environment" / "prior-traces" / "transcripts"
    assert sorted(p.stem for p in transcripts.glob("*.jsonl")) == ["s20"]
    config = yaml.safe_load((dest / "job-config-run-23.yaml").read_text())
    assert [Path(t["path"]).name for t in config["tasks"]] == ["peasant-pr-0023"]
    # The environment healthcheck warms the pre-PR Go build before the offline phases.
    environment = tomllib.loads((task / "task.toml").read_text())["environment"]
    assert "go mod download" in environment["healthcheck"]["command"]
    assert "safe.directory" in environment["healthcheck"]["command"]


def test_missing_part_fails_that_task_and_others_build(pipeline_env, tmp_path, capsys) -> None:
    dest = tmp_path / "out"
    code = _cli(pipeline_env, dest,
                "--prs", f"{LIVE}#21", "--prs", f"{LIVE}#22", "--prs", f"{LIVE}#99", "--run-id", "r")
    captured = capsys.readouterr()
    assert code == 1
    assert f"failed {LIVE}#21  part tests/golden:" in captured.out
    assert f"failed {LIVE}#99  part payload:" in captured.out
    assert "not in the corpus" in captured.out
    assert f"ok     {LIVE}#22" in captured.out
    assert "2 of 3 tasks failed" in captured.err
    assert (dest / "tasks" / "peasant-pr-0022" / "solution" / "oracle.patch").is_file()
    config = yaml.safe_load((dest / "job-config-r.yaml").read_text())
    assert [Path(t["path"]).name for t in config["tasks"]] == ["peasant-pr-0022"]
    assert config["agents"] == [{"name": "oracle", "model_name": None, "kwargs": {},
                                 "env": {"TRACEBENCH_RUN_ID": "r"}}]


def test_job_config_carries_run_id_attempts_and_env(pipeline_env, tmp_path) -> None:
    configs = TESTDATA / "target_configurations.yaml"
    dest = tmp_path / "out"
    assert _cli(pipeline_env, dest, "--prs", f"{LIVE}#20",
                "--target-configs", str(configs), "--target-config", "opencode-gpt-medium",
                "--job-config-format", "json") == 0
    [path] = dest.glob("job-config-*.json")
    config = json.loads(path.read_text())
    run_id = config["job_name"]
    assert path.name == f"job-config-{run_id}.json"
    parsed_run_id = uuid.UUID(run_id)
    assert parsed_run_id.version == 7
    assert config["n_attempts"] == N_ATTEMPTS == 3
    assert config["metrics"] == [{"type": "mean"}, {"type": "min"}, {"type": "max"}]
    assert config["tasks"] == [{"path": str((dest / "tasks" / "peasant-pr-0020").resolve()), "source": run_id}]
    assert config["agents"] == [{
        "name": "opencode", "model_name": "gpt-5.2-codex",
        "kwargs": {"reasoning_effort": "medium"}, "env": {"TRACEBENCH_RUN_ID": run_id},
    }]
    assert config["verifier"] == {"env": {"TRACEBENCH_RUN_ID": run_id}}
    assert RUN_ID_ENV == "TRACEBENCH_RUN_ID"


@pytest.mark.parametrize("case", FIXTURE["run_ids"], ids=lambda c: c["name"])
def test_new_run_id_is_a_unique_uuid7(case) -> None:
    prefix = f"{case['label']}-" if case["label"] else ""
    run_id = new_run_id(case["label"])
    assert run_id.startswith(prefix)
    parsed = uuid.UUID(run_id[len(prefix):])
    assert parsed.version == 7
    assert parsed.variant == uuid.RFC_4122
    assert run_id != new_run_id(case["label"])


def test_task_set_revision_is_stable_and_sensitive() -> None:
    case = FIXTURE["task_set_revision"]
    revision = task_set_revision(case["pr_ids"], case["index"])
    assert revision == case["revision"]
    assert task_set_revision(list(case["pr_ids"]), dict(case["index"])) == revision
    for variant in case["changed"]:
        assert task_set_revision(variant["pr_ids"], variant["index"]) != revision, variant["name"]


def test_empty_run_id_counts_as_absent(standard_dump) -> None:
    with pytest.raises(PipelineError, match="--run-id"):
        run_pipeline(Corpus(standard_dump), [f"{LIVE}#22"], "/nonexistent", repo_dir=".",
                     pr_index={}, specs=DEFAULT_REPOSITORY_SPECS, run_id="")


def test_run_label_with_run_id_is_an_error(standard_dump) -> None:
    with pytest.raises(PipelineError, match="--run-label"):
        run_pipeline(Corpus(standard_dump), [f"{LIVE}#22"], "/nonexistent", repo_dir=".",
                     pr_index={}, specs=DEFAULT_REPOSITORY_SPECS, run_id="r", run_label="nightly")


def test_yaml_format_without_pyyaml_fails_closed(standard_dump, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "yaml", None)  # blocks `import yaml`
    with pytest.raises(PipelineError, match="needs PyYAML"):
        run_pipeline(Corpus(standard_dump), [f"{LIVE}#22"], "/nonexistent", repo_dir=".",
                     pr_index={}, specs=DEFAULT_REPOSITORY_SPECS, run_id="r",
                     job_config_format="yaml")


def test_agent_without_harness_fails_before_building(standard_dump, tmp_path) -> None:
    with pytest.raises(PipelineError, match="no harness"):
        run_pipeline(Corpus(standard_dump), [f"{LIVE}#22"], tmp_path / "out", repo_dir=".",
                     pr_index={}, specs=DEFAULT_REPOSITORY_SPECS,
                     target_config=TargetConfiguration("bare"))
    assert not (tmp_path / "out").exists()


def test_cli_without_run_id_or_target_config_exits_2(pipeline_env, tmp_path, capsys) -> None:
    code = _cli(pipeline_env, tmp_path / "out", "--prs", f"{LIVE}#20")
    assert code == 2
    assert "--run-id" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_cli_run_label_with_run_id_exits_2(pipeline_env, tmp_path, capsys) -> None:
    code = _cli(pipeline_env, tmp_path / "out", "--prs", f"{LIVE}#20",
                "--run-id", "r", "--run-label", "nightly")
    assert code == 2
    assert "--run-label" in capsys.readouterr().err


def _run(env: dict, dest: Path, pr_ids: list[str], **kwargs):
    return run_pipeline(Corpus(env["dump"]), pr_ids, dest, repo_dir=env["repo"],
                        pr_index=json_index(env["index"]),
                        specs=kwargs.pop("specs", DEFAULT_REPOSITORY_SPECS),
                        run_id=kwargs.pop("run_id", "r"), **kwargs)


def json_index(path: Path) -> dict:
    return {f"{r['repo']}#{r['number']}": r for r in json.loads(path.read_text())}


def test_all_failed_run_writes_no_job_config(pipeline_env, tmp_path, capsys) -> None:
    dest = tmp_path / "out"
    code = _cli(pipeline_env, dest, "--prs", f"{LIVE}#99", "--run-id", "r")
    out = capsys.readouterr().out
    assert code == 1
    assert "no job config written: no task succeeded" in out
    assert not list(dest.glob("job-config*"))


def test_missing_repository_spec_names_the_part(pipeline_env, tmp_path) -> None:
    result = _run(pipeline_env, tmp_path / "out", [f"{LIVE}#20"], specs=())
    [task] = result.tasks
    assert (task.status, task.failed_part) == ("failed", "repository spec")
    assert "no repository spec matches" in task.error
    assert result.job_config is None


def test_malformed_corpus_record_names_it_and_others_build(pipeline_env, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline_module, "materialize_worktree",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("stop after payload")))
    corpus = Corpus(pipeline_env["dump"])
    corpus.pull_requests[f"{LIVE}#20"]["number"] = None
    result = run_pipeline(corpus, [f"{LIVE}#20", f"{LIVE}#22"], tmp_path / "out",
                          repo_dir=pipeline_env["repo"], pr_index=json_index(pipeline_env["index"]),
                          specs=DEFAULT_REPOSITORY_SPECS, run_id="r")
    bad, other = result.tasks
    assert (bad.failed_part, bad.status) == ("payload", "failed")
    assert f"malformed corpus record for pull request {LIVE}#20" in bad.error
    assert "None" in bad.error
    # The other task got past the payload step to the next dependency.
    assert other.failed_part == "environment/repo"
    assert other.error == "stop after payload"


def _boom(message: str):
    def fail(*_args, **_kwargs):
        raise ValueError(message)
    return fail


@pytest.mark.parametrize(
    ("dependency", "part", "stub_worktree"),
    [
        ("materialize_worktree", "environment/repo", False),
        ("build_oracle", "solution/oracle.patch", True),
        ("build_skeleton", "task", True),
    ],
)
def test_dependency_failure_names_its_part(pipeline_env, tmp_path, monkeypatch,
                                           dependency, part, stub_worktree) -> None:
    if stub_worktree:
        monkeypatch.setattr(pipeline_module, "materialize_worktree", lambda *a, **k: None)
    if dependency == "build_skeleton":
        monkeypatch.setattr(pipeline_module, "build_oracle", lambda *a, **k: object())
        monkeypatch.setattr(pipeline_module, "write_oracle", lambda *a, **k: None)
    monkeypatch.setattr(pipeline_module, dependency, _boom(f"{dependency} broke"))
    result = _run(pipeline_env, tmp_path / "out", [f"{LIVE}#20"])
    [task] = result.tasks
    assert (task.status, task.failed_part, task.error) == ("failed", part, f"{dependency} broke")
    assert result.job_config is None


def test_rerun_without_force_keeps_config_and_force_recovers(pipeline_env, tmp_path,
                                                             capsys) -> None:
    dest = tmp_path / "out"
    assert _cli(pipeline_env, dest, "--prs", f"{LIVE}#20", "--run-id", "r") == 0
    config_path = dest / "job-config-r.yaml"
    good = config_path.read_bytes()
    capsys.readouterr()

    assert _cli(pipeline_env, dest, "--prs", f"{LIVE}#20", "--run-id", "r") == 1
    out = capsys.readouterr().out
    assert "not empty" in out and "no job config written" in out
    assert config_path.read_bytes() == good

    assert _cli(pipeline_env, dest, "--prs", f"{LIVE}#22", "--run-id", "other") == 0
    assert config_path.read_bytes() == good
    assert (dest / "job-config-other.yaml").is_file()

    assert _cli(pipeline_env, dest, "--prs", f"{LIVE}#20", "--run-id", "r",
                "--force") == 0
    assert f"ok     {LIVE}#20" in capsys.readouterr().out
    assert yaml.safe_load(config_path.read_text())["job_name"] == "r"


def test_run_id_is_required_without_target_config(standard_dump) -> None:
    with pytest.raises(PipelineError, match="--run-id"):
        run_pipeline(Corpus(standard_dump), [f"{LIVE}#22"], "/nonexistent", repo_dir=".",
                     pr_index={}, specs=DEFAULT_REPOSITORY_SPECS)


def test_pr_list_merges_flags_and_files(tmp_path) -> None:
    listing = tmp_path / "prs.txt"
    listing.write_text(f"{LIVE}#1\n# comment\n\n{LIVE}#2\n")
    assert read_pr_list([str(listing), f"{LIVE}#3", f"{LIVE}#1"]) == [f"{LIVE}#1", f"{LIVE}#2", f"{LIVE}#3"]
    with pytest.raises(PipelineError, match="neither a pull request id"):
        read_pr_list([str(tmp_path / "missing.txt")])
    with pytest.raises(PipelineError, match="empty"):
        empty = tmp_path / "empty.txt"
        empty.write_text("\n")
        read_pr_list([str(empty)])


@pytest.mark.parametrize("line", FIXTURE["malformed_pr_lines"])
def test_malformed_pr_list_line_is_rejected(tmp_path, line) -> None:
    listing = tmp_path / "prs.txt"
    listing.write_text(f"{LIVE}#1\n{line}\n")
    with pytest.raises(PipelineError, match="is not a pull request id") as info:
        read_pr_list([str(listing)])
    assert repr(line) in str(info.value)

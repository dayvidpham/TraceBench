"""The pipeline driver: PR list -> runnable Harbor tasks + a Harbor job config."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from conftest import LIVE, TESTDATA, envelope, metadata

from tracebench_corpus import Corpus, find_target_config
from tracebench_corpus.cli import main
from tracebench_corpus.pipeline import (
    N_ATTEMPTS,
    REQUIRED_TASK_PARTS,
    RUN_ID_ENV,
    PipelineError,
    derive_run_id,
    read_pr_list,
    run_pipeline,
)
from tracebench_corpus.repository_spec import DEFAULT_REPOSITORY_SPECS
from tracebench_corpus.worktree import SNAPSHOT_MODULE

FIXTURE = yaml.safe_load((TESTDATA / "pipeline.yaml").read_text())

needs_go = pytest.mark.skipif(shutil.which("go") is None, reason="needs the Go toolchain")


@pytest.fixture(scope="session")
def snapshot_bin(tmp_path_factory) -> Path:
    if shutil.which("go") is None:
        pytest.skip("needs the Go toolchain")
    out = tmp_path_factory.mktemp("bin") / "snapshot"
    subprocess.run(["go", "build", "-o", str(out), "./cmd/snapshot"], cwd=SNAPSHOT_MODULE, check=True)
    return out


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
        git("commit", "-q", "-m", spec["name"])
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
    index = [{"repo": LIVE, "number": n, "merge_commit": merges[n]} for n in numbers]
    index_path = tmp_path / "merged_prs.json"
    index_path.write_text(json.dumps(index))
    return {"repo": repo, "dump": dump, "index": index_path, "merges": merges}


def _cli(env: dict, dest: Path, snapshot_bin: Path, *extra: str) -> int:
    return main([
        "--corpus", str(env["dump"]), "pipeline",
        "--repo-dir", str(env["repo"]), "--index", str(env["index"]), "--dest", str(dest),
        "--snapshot-bin", str(snapshot_bin), *extra,
    ])


@needs_go
def test_two_prs_build_two_runnable_tasks(pipeline_env, tmp_path, snapshot_bin, capsys) -> None:
    pr_file = tmp_path / "prs.txt"
    pr_file.write_text(f"# the task set\n{LIVE}#20\n\n")
    dest = tmp_path / "out"
    assert _cli(pipeline_env, dest, snapshot_bin, "--prs", str(pr_file), "--prs", f"{LIVE}#22",
                "--run-id", "run-x") == 0
    out = capsys.readouterr().out
    assert set(REQUIRED_TASK_PARTS) == set(FIXTURE["required_parts"])
    for pr_id, name in FIXTURE["expected_tasks"].items():
        task = dest / "tasks" / name
        assert f"ok     {pr_id}" in out
        for part in FIXTURE["required_parts"]:
            assert (task / part).exists(), f"{name} lacks {part}"
        assert not (task / "environment" / "repo" / ".git").exists()
        assert (task / "tests" / "golden" / "greet_test.go").is_file()
    # The worktree is the pre-PR tree: #22's repo has #20's greet.go, not #22's.
    repo22 = dest / "tasks" / "peasant-pr-0022" / "environment" / "repo"
    assert "hi" in (repo22 / "greet.go").read_text()
    assert "hello" in (dest / "tasks" / "peasant-pr-0022" / "tests" / "golden" / "greet_test.go").read_text()
    patch = (dest / "tasks" / "peasant-pr-0022" / "solution" / "oracle.patch").read_text()
    assert "greet.go" in patch


@needs_go
def test_missing_part_fails_that_task_and_others_build(pipeline_env, tmp_path, snapshot_bin, capsys) -> None:
    dest = tmp_path / "out"
    code = _cli(pipeline_env, dest, snapshot_bin,
                "--prs", f"{LIVE}#21", "--prs", f"{LIVE}#22", "--prs", f"{LIVE}#99", "--run-id", "r")
    captured = capsys.readouterr()
    assert code == 1
    assert f"failed {LIVE}#21  part tests/golden:" in captured.out
    assert f"failed {LIVE}#99  part payload:" in captured.out
    assert "not in the corpus" in captured.out
    assert f"ok     {LIVE}#22" in captured.out
    assert "2 of 3 tasks failed" in captured.err
    assert (dest / "tasks" / "peasant-pr-0022" / "solution" / "oracle.patch").is_file()
    config = yaml.safe_load((dest / "job-config.yaml").read_text())
    assert [Path(t["path"]).name for t in config["tasks"]] == ["peasant-pr-0022"]


@needs_go
def test_job_config_carries_run_id_attempts_and_env(pipeline_env, tmp_path, snapshot_bin) -> None:
    configs = TESTDATA / "target_configurations.yaml"
    dest = tmp_path / "out"
    assert _cli(pipeline_env, dest, snapshot_bin, "--prs", f"{LIVE}#20",
                "--target-configs", str(configs), "--target-config", "opencode-gpt-medium",
                "--job-config-format", "json") == 0
    config = json.loads((dest / "job-config.json").read_text())
    run_id = config["job_name"]
    assert run_id.startswith("tracebench-opencode-gpt-medium-")
    assert config["n_attempts"] == N_ATTEMPTS == 3
    assert config["tasks"] == [{"path": str((dest / "tasks" / "peasant-pr-0020").resolve()), "source": run_id}]
    assert config["agents"] == [{
        "name": "opencode", "model_name": "gpt-5.2-codex",
        "kwargs": {"reasoning_effort": "medium"}, "env": {RUN_ID_ENV: run_id},
    }]
    assert config["verifier"] == {"env": {RUN_ID_ENV: run_id}}


@pytest.mark.parametrize("case", FIXTURE["run_ids"], ids=lambda c: c["config"])
def test_derived_run_id(case, target_config_spec) -> None:
    config = find_target_config(target_config_spec, case["config"])
    run_id = derive_run_id(config, "rev1", case["label"])
    key = f"{config.harness}||{config.model}|{config.thinking}|rev1"
    assert run_id == case["prefix"] + hashlib.sha256(key.encode()).hexdigest()[:12]
    assert derive_run_id(config, "rev2", case["label"]) != run_id


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

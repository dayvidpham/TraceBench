"""Batch driver: build one runnable Harbor task per pull request.

For each pull request, in order, the driver runs:

1. ``payload`` -- ``TaskBuilder.build`` with the merged-state golden suite;
2. ``environment/repo`` -- the secure worktree at ``tree_commit``;
3. ``solution/oracle.patch`` -- the verified oracle and ``solve.sh``;
4. ``task`` -- the Harbor skeleton with the spec's test command.

A failure in one part fails that task, names the part, and leaves the other
tasks running. The driver then writes a Harbor job config for the successful
tasks (none when no task built). It never runs Harbor: running the job belongs to the eval pipeline.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .corpus import Corpus
from .golden import GoldenSuiteError
from .oracle import build_oracle, payload_commits, payload_request, write_oracle
from .repository_spec import RepositorySpec, find_repository_spec
from .skeleton import build_skeleton, task_dir_name
from .target_config import TargetConfiguration
from .task import TaskBuilder
from .worktree import materialize_worktree

#: Attempts per task in the emitted job config (repeats are pooled by run id).
N_ATTEMPTS = 3
#: Environment variable that carries the run id into agents and the verifier.
RUN_ID_ENV = "TRACEBENCH_RUN_ID"
#: Parts every built task directory must contain, relative to the task root.
REQUIRED_TASK_PARTS = (
    "task.toml",
    "instruction.md",
    "environment/repo",
    "environment/prior-traces",
    "tests/golden",
    "tests/test.sh",
    "solution/oracle.patch",
    "solution/solve.sh",
)
#: Aggregate metrics over the attempts of each task (repeats design).
METRICS = ({"type": "mean"}, {"type": "min"}, {"type": "max"})
#: Job config formats ``write_job_config`` accepts.
JOB_CONFIG_FORMATS = ("yaml", "json")

STATUS_OK = "ok"
STATUS_FAILED = "failed"


class PipelineError(ValueError):
    """The pipeline cannot start (bad PR list, run id, agent, format, or arguments).

    Raised before any task is built; per-task failures are reported in
    ``TaskResult`` instead.
    """


@dataclass
class TaskResult:
    """The outcome of building one pull request's task."""

    pr_id: str
    status: str
    task_dir: Path | None = None
    payload_dir: Path | None = None
    failed_part: str | None = None
    error: str | None = None

    def summary_line(self) -> str:
        if self.status == STATUS_OK:
            return f"{STATUS_OK:6} {self.pr_id}  {self.task_dir}"
        return f"{STATUS_FAILED:6} {self.pr_id}  part {self.failed_part}: {self.error}"


@dataclass
class PipelineResult:
    run_id: str
    tasks: list[TaskResult] = field(default_factory=list)
    job_config: Path | None = None

    @property
    def ok(self) -> bool:
        return all(task.status == STATUS_OK for task in self.tasks)


def read_pr_list(entries: list[str]) -> list[str]:
    """Expand ``--prs`` values: each is a PR id or a newline-delimited file of ids.

    Blank lines and ``#`` comments in files are skipped; duplicates keep the
    first position.
    """
    ids: list[str] = []
    for entry in entries:
        path = Path(entry)
        if "#" not in entry or path.is_file():
            try:
                lines = path.read_text().splitlines()
            except OSError as exc:
                raise PipelineError(
                    f"pipeline: --prs {entry!r} is neither a pull request id (owner/repo#N) "
                    f"nor a readable PR list file ({exc}); pass an id or a file with one id per line"
                ) from None
            candidates = [line.strip() for line in lines]
            candidates = [line for line in candidates if line and not line.startswith("#")]
        else:
            candidates = [entry.strip()]
        for candidate in candidates:
            if "#" not in candidate:
                raise PipelineError(
                    f"pipeline: {candidate!r} from --prs {entry!r} is not a pull request id; "
                    "use the owner/repo#N form, one per line"
                )
            if candidate not in ids:
                ids.append(candidate)
    if not ids:
        raise PipelineError("pipeline: the PR list is empty; pass --prs ID or --prs FILE")
    return ids


def task_set_revision(pr_ids: list[str], pr_index: dict[str, dict[str, Any]]) -> str:
    """Stable revision of a task set: the ordered PR ids with their merge commits."""
    lines = [f"{pr_id}@{(pr_index.get(pr_id) or {}).get('merge_commit') or ''}" for pr_id in pr_ids]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def derive_run_id(config: TargetConfiguration, revision: str, label: str | None = None) -> str:
    """``<label>-<first 12 hex of sha256(harness|model|thinking|revision)>``.

    A provider prefix (``anthropic/claude-sonnet-5``) stays part of the model
    name, so the provider is covered without a separate term.
    The label defaults to ``tracebench-<configuration name>``.
    """
    key = "|".join([config.harness or "", config.model or "", config.thinking or "", revision])
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"{label or f'tracebench-{config.name}'}-{digest}"


def job_agent(config: TargetConfiguration | None) -> dict[str, Any]:
    """The Harbor agent block for ``config`` (the ``oracle`` agent when absent)."""
    if config is not None:
        if not config.harness:
            raise PipelineError(
                f"pipeline: target configuration {config.name!r} has no harness; the job "
                "config needs an agent name. Add `harness:` to the target-configuration spec."
            )
        agent: dict[str, Any] = {
            "name": config.harness,
            "model_name": config.model,
            "kwargs": {"reasoning_effort": config.thinking} if config.thinking else {},
        }
    else:
        agent = {"name": "oracle", "model_name": None, "kwargs": {}}
    return agent


def job_config(
    run_id: str, task_dirs: list[Path], config: TargetConfiguration | None
) -> dict[str, Any]:
    """The Harbor job config for ``task_dirs`` under ``run_id``."""
    env = {RUN_ID_ENV: run_id}
    agent = job_agent(config)
    agent["env"] = dict(env)
    return {
        "job_name": run_id,
        "n_attempts": N_ATTEMPTS,
        "metrics": [dict(metric) for metric in METRICS],
        "tasks": [{"path": str(path), "source": run_id} for path in task_dirs],
        "agents": [agent],
        "verifier": {"env": dict(env)},
    }


def resolve_job_config_format(fmt: str | None) -> str:
    """``yaml`` or ``json``; the default is yaml when PyYAML imports, else json."""
    if fmt is None:
        try:
            import yaml
        except ImportError:
            return "json"
        return "yaml"
    if fmt not in JOB_CONFIG_FORMATS:
        raise PipelineError(f"pipeline: unknown job config format {fmt!r}; use yaml or json")
    if fmt == "yaml":
        try:
            import yaml
        except ImportError:
            raise PipelineError(
                "pipeline: --job-config-format yaml needs PyYAML, which is not installed; "
                "install it (pip install pyyaml) or pass --job-config-format json"
            ) from None
    return fmt


def write_job_config(dest: Path, config: dict[str, Any], fmt: str | None = None) -> Path:
    """Write ``job-config-<run id>.yaml`` or ``.json`` into ``dest``.

    The file name carries the run id (``config["job_name"]``) so task sets with
    different run ids in one destination never overwrite each other.
    """
    fmt = resolve_job_config_format(fmt)
    dest.mkdir(parents=True, exist_ok=True)
    stem = f"job-config-{config['job_name']}"
    if fmt == "yaml":
        import yaml

        path = dest / f"{stem}.yaml"
        path.write_text(yaml.safe_dump(config, sort_keys=False))
    else:
        path = dest / f"{stem}.json"
        path.write_text(json.dumps(config, indent=2) + "\n")
    return path


def _missing_parts(task_dir: Path) -> list[str]:
    return [part for part in REQUIRED_TASK_PARTS if not (task_dir / part).exists()]


def build_task(
    builder: TaskBuilder,
    pr_id: str,
    dest: Path,
    *,
    repo_dir: Path,
    specs: list[RepositorySpec] | tuple[RepositorySpec, ...],
    target_config: TargetConfiguration | None = None,
    snapshot_bin: str | Path | None = None,
    force: bool = False,
) -> TaskResult:
    """Build one task; never raises for a per-task failure."""
    part = "payload"
    result = TaskResult(pr_id, STATUS_FAILED)
    try:
        pr = builder.corpus.pull_requests.get(pr_id)
        if pr is None:
            raise KeyError(f"pull request {pr_id} is not in the corpus; check the PR list")
        try:
            name = task_dir_name(pr["repo"], pr["number"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"malformed corpus record for pull request {pr_id}: {exc}; "
                "fix its `repo` and `number` fields in the corpus"
            ) from None
        payload_dir = result.payload_dir = dest / "payloads" / name
        task_dir = result.task_dir = dest / "tasks" / name
        part = "repository spec"
        spec = find_repository_spec(specs, pr["repo"])
        part = "payload"
        if force and payload_dir.exists():
            shutil.rmtree(payload_dir)
        try:
            builder.build(pr_id, payload_dir, target_config=target_config)
        except GoldenSuiteError:
            part = "tests/golden"
            raise
        if not any((payload_dir / "tests").iterdir()):
            part = "tests/golden"
            raise ValueError(f"payload {payload_dir} has an empty tests/ golden suite")

        part = "environment/repo"
        materialize_worktree(repo_dir, payload_dir, snapshot_bin=snapshot_bin)

        part = "solution/oracle.patch"
        request = payload_request(payload_dir)
        tree_commit, merge_commit = payload_commits(payload_dir, repo_dir, request)
        write_oracle(payload_dir, build_oracle(repo_dir, tree_commit, merge_commit, spec.build_command))

        part = "task"
        build_skeleton(payload_dir, task_dir, test_command=spec.test_command, force=force)
        missing = _missing_parts(task_dir)
        if missing:
            part = missing[0]
            raise ValueError(
                f"task {task_dir} lacks {', '.join(missing)} after the skeleton step; "
                "check that the payload carries every part"
            )
    except (KeyError, ValueError, OSError) as exc:
        message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
        result.failed_part = part
        result.error = str(message)
        return result
    result.status = STATUS_OK
    return result


def run_pipeline(
    corpus: Corpus,
    pr_ids: list[str],
    dest: str | Path,
    *,
    repo_dir: str | Path,
    pr_index: dict[str, dict[str, Any]],
    specs: list[RepositorySpec] | tuple[RepositorySpec, ...],
    target_config: TargetConfiguration | None = None,
    run_id: str | None = None,
    run_label: str | None = None,
    snapshot_bin: str | Path | None = None,
    job_config_format: str | None = None,
    force: bool = False,
) -> PipelineResult:
    """Build every task, then write the job config for the tasks that succeeded.

    No job config is written when no task built (``result.job_config`` is None).
    """
    if not run_id:
        run_id = None
    if run_id is not None and run_label:
        raise PipelineError(
            "pipeline: --run-label only applies when the run id is derived; pass either "
            "--run-id or --run-label, not both"
        )
    if run_id is None:
        if target_config is None:
            raise PipelineError(
                "pipeline: no run id; pass --run-id, or --target-configs with --target-config "
                "so the run id is derived from the target configuration"
            )
        run_id = derive_run_id(target_config, task_set_revision(pr_ids, pr_index), run_label)
    # Validate the agent and format now so a bad argument fails before building.
    job_agent(target_config)
    job_config_format = resolve_job_config_format(job_config_format)
    dest = Path(dest).resolve()
    repo_dir = Path(repo_dir).resolve()
    builder = TaskBuilder(corpus, pr_index=pr_index, repo_dir=repo_dir, materialize_tests=True)
    result = PipelineResult(run_id=run_id)
    for pr_id in pr_ids:
        result.tasks.append(
            build_task(
                builder, pr_id, dest, repo_dir=repo_dir, specs=specs,
                target_config=target_config, snapshot_bin=snapshot_bin, force=force,
            )
        )
    built = [task.task_dir for task in result.tasks if task.status == STATUS_OK and task.task_dir]
    if built:
        result.job_config = write_job_config(
            dest, job_config(run_id, built, target_config), job_config_format
        )
    return result

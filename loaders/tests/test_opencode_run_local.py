"""Exercise the local Harbor launch boundary without starting a container."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from harness.opencode.run_local import AUTH_TARGET, XDG_DATA_HOME
from tracebench_corpus.pipeline import job_config, write_job_config
from tracebench_corpus.target_config import find_target_config, load_target_configs


TARGET_SPEC = Path(__file__).resolve().parents[2] / "harness/opencode/target-configurations.yaml"
CASES = yaml.safe_load((Path(__file__).parent / "testdata/opencode_local_jobs.yaml").read_text())


def test_rejection_fixture_coverage() -> None:
    assert {case["name"] for case in CASES["invalid_auth_paths"]} == {"missing-file", "directory"}
    assert {case["name"] for case in CASES["rejected_jobs"]} == {
        "other-agent", "mixed-agents", "user-agent",
    }


def _generated_job(tmp_path: Path, fmt: str = "json") -> Path:
    targets = load_target_configs(TARGET_SPEC)
    target = find_target_config(targets, "opencode-oauth")
    config = job_config("auth-mount-test", [tmp_path / "task"], target)
    return write_job_config(tmp_path, config, fmt)


def _fake_harbor(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    executable = tmp_path / "harbor"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['HARBOR_ARGV_CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"
    )
    executable.chmod(0o755)
    capture = tmp_path / "argv.json"
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    env["HARBOR_ARGV_CAPTURE"] = str(capture)
    env.pop("TRACEBENCH_OPENCODE_AUTH_JSON", None)
    return capture, env


def _launch(job: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "harness.opencode.run_local", "--job-config", str(job), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_auth_file_is_only_in_a_read_only_runtime_mount(tmp_path: Path) -> None:
    job = _generated_job(tmp_path)
    original_job = job.read_bytes()
    auth = tmp_path / "auth.json"
    auth.write_text('{"fixture": "private-marker"}\n')
    capture, env = _fake_harbor(tmp_path)
    env["TRACEBENCH_OPENCODE_AUTH_JSON"] = str(auth)

    result = _launch(job, env)

    assert result.returncode == 0, result.stderr
    args = json.loads(capture.read_text())
    assert args[:5] == ["run", "-c", str(job), "-e", "podman"]
    assert args[args.index("--agent-env") + 1] == f"XDG_DATA_HOME={XDG_DATA_HOME}"
    mounts = json.loads(args[args.index("--mounts") + 1])
    assert mounts == [{
        "type": "bind",
        "source": str(auth.resolve()),
        "target": AUTH_TARGET,
        "read_only": True,
        "bind": {"create_host_path": False},
    }]
    assert job.read_bytes() == original_job
    assert b"private-marker" not in job.read_bytes()
    assert "private-marker" not in json.dumps(args)


@pytest.mark.parametrize("case", CASES["invalid_auth_paths"], ids=lambda case: case["name"])
def test_missing_or_non_file_auth_stops_before_harbor(tmp_path: Path, case: dict) -> None:
    job = tmp_path / "job-config.json"
    job.write_text("{}\n")
    capture, env = _fake_harbor(tmp_path)

    result = _launch(job, env, "--auth-json", str(tmp_path / case["path"]))
    assert result.returncode == 2
    assert not capture.exists()
    assert "auth.json" in result.stderr


@pytest.mark.parametrize("case", CASES["rejected_jobs"], ids=lambda case: case["name"])
def test_auth_is_not_mounted_into_other_agents(tmp_path: Path, case: dict) -> None:
    job = tmp_path / "job-config.json"
    auth = tmp_path / "auth.json"
    auth.write_text("{}\n")
    capture, env = _fake_harbor(tmp_path)

    job.write_text(json.dumps(case["config"]))
    result = _launch(job, env, "--auth-json", str(auth))
    assert result.returncode == 2
    assert "requires one" in result.stderr
    assert not capture.exists()


def test_generated_yaml_job_accepts_auth_mount(tmp_path: Path) -> None:
    job = _generated_job(tmp_path, "yaml")
    auth = tmp_path / "auth.json"
    auth.write_text("{}\n")
    capture, env = _fake_harbor(tmp_path)

    result = _launch(job, env, "--auth-json", str(auth))

    assert result.returncode == 0, result.stderr
    assert "--mounts" in json.loads(capture.read_text())


def test_no_auth_keeps_plain_local_launch(tmp_path: Path) -> None:
    job = tmp_path / "job-config.json"
    job.write_text("{}\n")
    capture, env = _fake_harbor(tmp_path)

    result = _launch(job, env)

    assert result.returncode == 0, result.stderr
    assert json.loads(capture.read_text()) == ["run", "-c", str(job), "-e", "podman"]

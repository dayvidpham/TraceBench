"""Fake-only tests of the launcher and installed Harbor container-exec boundary.

The installed agent, env resolver, scoped env, shell command builder and output
collectors are the subjects, not mocks. The container transport is a real local
subprocess fixture. No credential/provider network request is made.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from tracebench_corpus.local_run import (
    KEY_ENV, STREAM_GUARD, LocalRunError, launch, main, prepare_auth,
)

pytest.importorskip("harbor", reason="installed-Harbor contract requires Python >=3.12 and dev dependencies")
from harbor.agents.factory import AgentFactory
from harbor.environments.docker.docker_unix import UnixOps
from harbor.environments.docker.runtime import ContainerRuntime
from harbor.environments.podman import PodmanEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig
from harbor.models.task.config import EnvironmentConfig
from harbor.job import Job

DATA = yaml.safe_load((Path(__file__).parent / "testdata/auth_loading.yaml").read_text())
REQUIRED_NAMES = {
    "default-variable", "override-variable", "missing-variable", "empty-variable",
    "malformed-variable-name", "output-nondisclosure", "passthrough-to-opencode",
    "oracle/other-agent-no-auth",
}


def test_auth_fixture_manifest():
    names = [case["name"] for case in DATA["cases"]]
    assert set(DATA["required_names"]) == REQUIRED_NAMES
    assert set(names) == REQUIRED_NAMES
    assert len(names) == len(set(names))
    assert DATA["fake_server"] and DATA["model"].startswith("opencode-go/")


def configure(case, monkeypatch, tmp_path):
    # The tests never inherit a live selected variable, including on failures.
    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.delenv("TRACEBENCH_TEST_KEY", raising=False)
    for name, value in case["variables"].items():
        monkeypatch.setenv(name, value)
    return JobConfig.model_validate({
        "job_name": "auth-fixture", "jobs_dir": str(tmp_path),
        "environment": {"type": "podman"},
        "agents": [{"name": name, "model_name": DATA["model"] if name == "opencode" else None,
                    "env": {"TRACEBENCH_RUN_ID": "fixture-run"}}
                   for name in case["agents"]],
    })


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda case: case["name"])
def test_selected_variable_contract(case, monkeypatch, tmp_path, capsys):
    config = configure(case, monkeypatch, tmp_path)
    before = config.model_dump_json()
    variable = case.get("variable", KEY_ENV)
    if "error" in case:
        with pytest.raises(LocalRunError, match=case["error"]) as caught:
            prepare_auth(config, variable)
        assert config.model_dump_json() == before
        assert variable not in str(caught.value)
        path = tmp_path / "config.json"
        path.write_text(before)
        assert main(["-c", str(path), "--opencode-key-env", variable]) == 2
        assert "tracebench-fake" not in capsys.readouterr().err
        assert not (tmp_path / "auth-fixture").exists()
    elif "expected" not in case:
        assert prepare_auth(config, variable) is None
        assert config.model_dump_json() == before
    else:
        assert prepare_auth(config, variable) == case["expected"]
        for agent in config.agents:
            assert agent.env[KEY_ENV] == "${" + variable + "}"
            assert agent.model_name == DATA["model"]
        assert case["expected"] not in config.model_dump_json()
        assert "TRACEBENCH_RUN_ID" in config.model_dump_json()


@pytest.mark.parametrize("case", [case for case in DATA["cases"] if "expected" in case],
                         ids=lambda case: case["name"])
def test_installed_agent_exec_boundary(case, monkeypatch, tmp_path, caplog):
    config = configure(case, monkeypatch, tmp_path)
    key = prepare_auth(config, case.get("variable", KEY_ENV))
    logs = tmp_path / config.job_name / "logs"
    logs.mkdir(parents=True)
    executable = tmp_path / "bin" / "opencode"
    executable.parent.mkdir()
    executable.write_text(DATA["fake_server"])
    executable.chmod(0o755)
    # Host transport fixture represents a container without touching /logs or
    # installing a harness. All installed Harbor production wiring remains real.
    environment = object.__new__(PodmanEnvironment)
    environment.task_env_config = EnvironmentConfig(workdir=str(tmp_path))
    environment.default_user = None
    environment._persistent_env = {}
    environment._exec_env_overlays = contextvars.ContextVar("fixture_env", default=())
    environment._platform = UnixOps(environment)
    environment.session_id = "fixture"
    environment.environment_name = "fixture"
    environment.environment_dir = tmp_path
    monkeypatch.setattr(PodmanEnvironment, "_docker_compose_paths", property(lambda self: []))
    environment._compose_env_vars = lambda **kwargs: {
        "PATH": str(executable.parent) + os.pathsep + os.environ["PATH"],
        "HOME": str(tmp_path), "FAKE_ARTIFACT_DIR": str(logs),
        "FAKE_EXPECTED_KEY": key, "FAKE_EXIT_CODE": str(int(case.get("failure", False))),
    }
    monkeypatch.setattr(PodmanEnvironment, "runtime", classmethod(lambda cls: ContainerRuntime(
        engine=("podman",), compose=("fixture-compose",))))
    outputs = []
    async def callback(text, stream):
        outputs.append(text)
    environment._output_callback = lambda: callback
    spawn = asyncio.create_subprocess_exec
    commands = []
    private_mappings = []
    async def transport(*argv, **kwargs):
        commands.append(list(argv))
        if argv[0] == "fixture-compose":
            assert list(argv[-3:]) == ["ps", "-q", "main"]
            return await spawn("/bin/echo", DATA["container_resolution"], **kwargs)
        assert argv[:2] == ("podman", "exec")
        private_mappings.append(kwargs["env"][KEY_ENV] == key)
        assert KEY_ENV in argv and f"{KEY_ENV}={key}" not in argv
        # Execute the installed shell command against the fake container binary.
        # Its original absolute log paths are translated only in this transport.
        command = argv[-1].replace("/logs/agent", str(logs))
        return await spawn("/bin/bash", "-c", DATA["host_shell_prefix"] + command, **kwargs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", transport)
    caplog.set_level(logging.DEBUG)
    agent = AgentFactory.create_agent_from_config(config.agents[0], logs_dir=logs)
    async def run():
        with environment.scoped_exec_env(agent.extra_env):
            await agent.run("fake instruction", environment, AgentContext())
    class TransportJob:
        async def run(self):
            try:
                await asyncio.wait_for(run(), timeout=10)
            finally:
                # Observe persistence BEFORE the launcher's artifact guard.
                assert key not in (logs / "opencode.txt").read_text()
                logging.getLogger("fixture").warning("routine host failure: %s", key)
    async def create_job(cls, candidate):
        assert candidate is config
        assert key not in candidate.model_dump_json()
        return TransportJob()
    # Replace only job orchestration/transport, not the launcher or installed
    # auth/agent/exec subjects. The launcher's real finally guard owns cleanup.
    monkeypatch.setattr(Job, "create", classmethod(create_job))
    # prepare_auth's fresh-directory check precedes job creation.
    logs.parent.rename(tmp_path / "pending")
    async def create_with_output(cls, candidate):
        (tmp_path / "pending").rename(logs.parent)
        return await create_job(cls, candidate)
    monkeypatch.setattr(Job, "create", classmethod(create_with_output))
    if case.get("failure"):
        with pytest.raises(LocalRunError) as caught:
            asyncio.run(launch(config, case.get("variable", KEY_ENV)))
        assert key not in str(caught.value)
    else:
        asyncio.run(launch(config, case.get("variable", KEY_ENV)))
    assert private_mappings and all(private_mappings)
    assert (logs / "receipt.txt").read_text() == "received"
    assert "[REDACTED]" in (logs / "opencode.txt").read_text()
    assert key not in json.dumps(commands)
    assert all(key not in item for command in commands for item in command)
    assert key not in "".join(outputs)
    assert key not in caplog.text
    assert key not in config.model_dump_json()
    assert all(key.encode() not in path.read_bytes() for path in logs.rglob("*") if path.is_file())
    assert asyncio.create_subprocess_exec is transport


@pytest.mark.parametrize("case", [case for case in DATA["cases"] if "expected" in case],
                         ids=lambda case: case["name"])
def test_runtime_stream_guard_handles_split_matches(case):
    # A real child process exercises the exact program inserted before tee.
    key = case["expected"]
    process = subprocess.Popen(["python3", "-c", STREAM_GUARD],
                               env={"PATH": os.environ["PATH"], KEY_ENV: key},
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    prefix = "x" * DATA["stream_boundary"] + "before "
    try:
        stdout, stderr = process.communicate((prefix + key + " after").encode(), timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    assert process.returncode == 0 and not stderr
    assert stdout == prefix.encode() + b"[REDACTED] after"

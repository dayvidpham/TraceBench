"""Environment-only credentials for a local installed-Harbor job.

Task generation never imports this module. The adapter is process-owned by this
launcher and task-local at the Podman subprocess boundary; it never changes
``os.environ``. It supports the tested Harbor 0.23.0 runtime only.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import contextvars
import json
import logging
import os
import re
import shlex
import sys
import threading
from importlib.metadata import version
from pathlib import Path

KEY_ENV = "OPENCODE_API_KEY"
SUPPORTED_HARBOR = "0.23.0"
_ADAPTER_OWNER = threading.Lock()

# This program runs in the container. The credential is read from the runtime
# environment, never interpolated into the program or its arguments. Buffer a
# possible partial match between reads, including an unterminated last line.
STREAM_GUARD = """import json, os, sys
key = os.environ['OPENCODE_API_KEY']
forms = sorted({key.encode(), *(json.dumps(key, ensure_ascii=ascii)[1:-1].encode()
                              for ascii in (True, False))}, key=len, reverse=True)
def clean(data):
    for form in forms:
        data = data.replace(form, b'[REDACTED]')
    return data
pending = b''
while True:
    chunk = os.read(0, 65536)
    if not chunk:
        sys.stdout.buffer.write(clean(pending))
        sys.stdout.buffer.flush()
        break
    pending += chunk
    cut = max(0, len(pending) - len(forms[0]) + 1)
    while True:
        previous = cut
        for form in forms:
            hit = pending.find(form)
            while hit != -1 and hit < cut:
                cut = max(cut, hit + len(form))
                hit = pending.find(form, hit + len(form))
        if cut == previous:
            break
    sys.stdout.buffer.write(clean(pending[:cut]))
    sys.stdout.buffer.flush()
    pending = pending[cut:]
"""


class LocalRunError(ValueError):
    """A sanitized failure at the local launch boundary."""


def _credential_forms(key: str) -> list[str]:
    """Representations used by routine raw and JSON output."""
    return sorted({key, *(json.dumps(key, ensure_ascii=ascii)[1:-1]
                          for ascii in (True, False))}, key=len, reverse=True)


def prepare_auth(config, variable: str = KEY_ENV) -> str | None:
    """Add only a named reference for installed OpenCode; return a private key.

    The returned value stays inside the launch process. Callers must not print,
    serialize, or expose it in exceptions. Other agents bypass this path.
    """
    agents = [agent for agent in config.agents
              if agent.name == "opencode" and agent.import_path is None]
    if not agents:
        return None
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
        raise LocalRunError("invalid OpenCode credential variable name")
    key = os.environ.get(variable)
    if not key:
        raise LocalRunError("selected OpenCode credential variable is absent or empty")
    if config.environment.type != "podman" or config.environment.import_path:
        raise LocalRunError("OpenCode credential passthrough requires local Podman")
    if Path(config.job_name).name != config.job_name or config.job_name in (".", ".."):
        raise LocalRunError("local credential launch needs a simple job name")
    if (config.jobs_dir / config.job_name).exists():
        raise LocalRunError("local credential launch needs a fresh job directory")
    for agent in agents:
        agent.env[KEY_ENV] = "${" + variable + "}"
    return key


def scrub_artifacts(root: Path, key: str) -> None:
    """Scrub downloaded text and binary output, without following symlinks.

    This is an output guard, not confidentiality from shell tools. The runtime
    stream guard prevents normal OpenCode stdout from reaching tee unredacted.
    Harbor also downloads server state and arbitrary task artifacts; guard those
    before returning job evidence, including on failure.
    """
    markers = [form.encode() for form in _credential_forms(key)]
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        content = path.read_bytes()
        guarded = content
        for marker in markers:
            guarded = guarded.replace(marker, b"[REDACTED]")
        if guarded != content:
            path.write_bytes(guarded)


@contextlib.contextmanager
def runtime_adapter(key: str):
    """Guard the installed agent and local Podman exec in this launcher process.

    Only subprocesses owned by Podman's Compose method receive the private
    per-call mapping. Parallel trials do not mutate a shared host environment.
    Patches are restored on exit; nested launchers in one process are excluded.
    """
    from harbor.environments.podman import PodmanEnvironment

    if version("harbor") != SUPPORTED_HARBOR:
        raise LocalRunError("unsupported Harbor runtime; use the documented version")
    create_process = asyncio.create_subprocess_exec
    compose = PodmanEnvironment._run_docker_compose_command
    exec_command = PodmanEnvironment.exec
    handle_record = logging.Logger.handle
    forms = _credential_forms(key)
    owned_processes = contextvars.ContextVar("credential_exec_processes", default=None)

    async def tracked_process(*args, **kwargs):
        process = await create_process(*args, **kwargs)
        processes = owned_processes.get()
        if processes is not None:
            processes.append(process)
        return process

    def clean(value):
        if isinstance(value, str):
            for form in forms:
                value = value.replace(form, "[REDACTED]")
            return value
        if isinstance(value, tuple):
            return tuple(clean(item) for item in value)
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {clean(name): clean(item) for name, item in value.items()}
        return value

    def guarded_record(logger, record):
        if record.exc_info:
            record.exc_text = clean(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        for name, value in list(record.__dict__.items()):
            record.__dict__[name] = clean(value)
        if isinstance(getattr(record, "env", None), dict) and KEY_ENV in record.env:
            record.env[KEY_ENV] = "[REDACTED]"
        handle_record(logger, record)

    async def guarded_command(environment, command, *args, deadline=None, **kwargs):
        def remaining():
            if deadline is None:
                return None
            duration = deadline - asyncio.get_running_loop().time()
            if duration <= 0:
                raise LocalRunError("local credential exec timed out")
            return duration

        callback = kwargs.get("on_output")
        if callback:
            async def guarded_output(text, stream):
                # Delay delivery until the complete output can be scrubbed.
                # Values can span lines; contents are deliberately not validated.
                pass
            kwargs["on_output"] = guarded_output
        try:
            command = list(command)
            assignment = f"{KEY_ENV}={key}"
            credential = any(index and command[index - 1] == "-e" and item == assignment
                             for index, item in enumerate(command))
            if credential:
                # Do not let a Compose frontend re-expand the named reference
                # into a literal engine argument. Resolve only the container ID
                # with Compose; invoke the engine's named-env exec directly.
                index = 1
                while index < len(command) and command[index] in ("-e", "-u", "-w"):
                    index += 2
                if command[0] != "exec" or index >= len(command):
                    raise LocalRunError("unsupported local credential exec command")
                service = command[index]
                resolved = await compose(environment, ["ps", "-q", service],
                                         timeout_sec=remaining())
                # Podman may prefix the ID with the ANSI reset from its Compose
                # provider banner, even when output is captured without a TTY.
                output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", resolved.stdout or "")
                containers = [line.strip() for line in output.splitlines()
                              if re.fullmatch(r"[a-fA-F0-9]{12,64}", line.strip())]
                if len(containers) != 1:
                    raise LocalRunError("could not resolve local container for credential exec")
                container = containers[0]
                options = [KEY_ENV if item == assignment else item
                           for item in command[1:index]]
                runtime = type(environment).runtime()
                argv = [*runtime.engine, "exec", *options, container, *command[index + 1:]]
                if kwargs.get("stdin_data") is not None:
                    argv.insert(len(runtime.engine) + 1, "-i")
                if any(key in item for item in argv):
                    raise LocalRunError("credential-bearing runtime argument rejected")
                env = environment._compose_env_vars(include_os_env=True)
                env[KEY_ENV] = key
                process = await tracked_process(
                    *argv, env=env,
                    stdin=asyncio.subprocess.PIPE if kwargs.get("stdin_data") is not None
                    else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                )
                collector = (environment._collect_streamed_output if callback
                             else environment._collect_buffered_output)
                collection = dict(timeout_sec=remaining(),
                                  stdin_data=kwargs.get("stdin_data"))
                if callback:
                    collection["on_output"] = kwargs["on_output"]
                result = await collector(process, **collection)
                if kwargs.get("check", True) and result.return_code:
                    raise LocalRunError("local credential exec failed")
            else:
                if any(key in item for item in command):
                    raise LocalRunError("credential-bearing runtime argument rejected")
                result = await compose(environment, command, *args, **kwargs)
            result.stdout = clean(result.stdout)
            result.stderr = clean(result.stderr)
            if callback and result.stdout:
                await callback(result.stdout, "stdout")
            return result
        except Exception as exc:
            raise LocalRunError(clean(str(exc))) from None

    async def guarded_compose(environment, command, *args, **kwargs):
        timeout = kwargs.get("timeout_sec")
        deadline = asyncio.get_running_loop().time() + timeout if timeout else None
        processes = []
        token = owned_processes.set(processes)
        try:
            async with asyncio.timeout_at(deadline):
                return await guarded_command(environment, command, *args,
                                             deadline=deadline, **kwargs)
        except TimeoutError:
            raise LocalRunError("local credential exec timed out") from None
        finally:
            owned_processes.reset(token)
            for process in processes:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()

    async def guarded_exec(environment, command, *args, **kwargs):
        # Keep the installed command and flags. Insert the guard before its
        # existing tee, so normal stdout never persists a credential marker.
        tee = "| stdbuf -oL tee /logs/agent/opencode.txt"
        if "opencode " in command and "run --format=json" in command:
            if tee not in command:
                raise LocalRunError("unsupported installed OpenCode output command")
            command = command.replace(tee, "| python3 -c " + shlex.quote(STREAM_GUARD) + " " + tee)
        return await exec_command(environment, command, *args, **kwargs)

    with contextlib.ExitStack() as stack:
        if not _ADAPTER_OWNER.acquire(blocking=False):
            raise LocalRunError("a local credential adapter already owns this process")
        stack.callback(_ADAPTER_OWNER.release)
        for target, name, replacement in (
            (asyncio, "create_subprocess_exec", tracked_process),
            (PodmanEnvironment, "_run_docker_compose_command", guarded_compose),
            (PodmanEnvironment, "exec", guarded_exec),
            (logging.Logger, "handle", guarded_record),
        ):
            previous = getattr(target, name)
            stack.callback(setattr, target, name, previous)
            setattr(target, name, replacement)
        yield


async def launch(config, variable: str = KEY_ENV):
    """Run an installed Harbor job; no task, model, or network-policy changes."""
    from harbor.job import Job

    key = prepare_auth(config, variable)
    if key is None:
        return await (await Job.create(config)).run()
    output = config.jobs_dir / config.job_name
    try:
        with runtime_adapter(key):
            job = await Job.create(config)
            return await job.run()
    except Exception:
        raise LocalRunError("local OpenCode job failed; inspect guarded job results") from None
    finally:
        try:
            scrub_artifacts(output, key)
        except Exception:
            raise LocalRunError("could not guard local job artifacts; do not publish them") from None


def main(argv: list[str] | None = None) -> int:
    """Launch a local Harbor job config with a variable-name-only credential."""
    parser = argparse.ArgumentParser(prog="tracebench-harbor")
    parser.add_argument("-c", "--config", type=Path, required=True,
                        help="Harbor YAML/JSON job config; set environment.type to podman")
    parser.add_argument("--opencode-key-env", default=KEY_ENV,
                        help="already-set host variable NAME (never a key value)")
    args = parser.parse_args(argv)
    try:
        from harbor.models.job.config import JobConfig
        import yaml

        config = JobConfig.model_validate(yaml.safe_load(args.config.read_text()))
        result = asyncio.run(launch(config, args.opencode_key_env))
    except ImportError:
        print("tracebench-harbor: install tracebench-corpus[harbor]", file=sys.stderr)
        return 2
    except LocalRunError as exc:
        print(f"tracebench-harbor: {exc}", file=sys.stderr)
        return 2
    except Exception:
        # Pydantic, YAML and downstream exceptions may carry user-supplied data.
        # Never print their raw input, repr, traceback or local variables.
        print("tracebench-harbor: local launch failed; check variable name, nonempty "
              "environment, config and guarded job results", file=sys.stderr)
        return 2
    print("local Harbor job finished; inspect guarded job results")
    return 1 if result.stats.n_errored_trials else 0


if __name__ == "__main__":
    raise SystemExit(main())

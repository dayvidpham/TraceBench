"""Launch a generated Harbor job with an optional local OpenCode login mount."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

XDG_DATA_HOME = "/tmp/tracebench-opencode-data"
AUTH_TARGET = f"{XDG_DATA_HOME}/opencode/auth.json"
AUTH_ENV = "TRACEBENCH_OPENCODE_AUTH_JSON"
OPENCODE_AGENT = "harness.opencode.agent:OpenCode"


def auth_source(value: str) -> Path:
    """Resolve a readable regular file before passing it to Podman."""
    try:
        path = Path(value).expanduser().resolve(strict=True)
        mode = path.stat().st_mode
    except OSError as exc:
        raise ValueError(f"OpenCode auth.json is unavailable: {exc}") from None
    if not stat.S_ISREG(mode) or not os.access(path, os.R_OK):
        raise ValueError("OpenCode auth.json must be a readable regular file")
    return path


def require_opencode_job(job_config: Path) -> None:
    """Do not expose the login to a different agent or a mixed-agent job."""
    try:
        import yaml
    except ImportError:
        raise ValueError("Reading Harbor job configs needs PyYAML") from None
    try:
        config = yaml.safe_load(job_config.read_text())
    except OSError as exc:
        raise ValueError(f"Cannot read Harbor job config: {exc}") from None
    except (UnicodeError, yaml.YAMLError):
        raise ValueError("Cannot parse Harbor job config") from None
    agents = config.get("agents") if isinstance(config, dict) else None
    if (
        not isinstance(agents, list)
        or len(agents) != 1
        or not isinstance(agents[0], dict)
        or agents[0].get("name") != OPENCODE_AGENT
        or (isinstance(config, dict) and config.get("user_agent") is not None)
    ):
        raise ValueError(f"Auth mount requires one {OPENCODE_AGENT} agent and no user agent")


def harbor_command(job_config: Path, auth_json: str | None) -> list[str]:
    """Build a local run command without putting credential bytes in the job file."""
    if not job_config.is_file():
        raise ValueError(f"Harbor job config is not a file: {job_config}")
    source = auth_source(auth_json) if auth_json is not None else None
    if source is not None:
        require_opencode_job(job_config)
    harbor = shutil.which("harbor")
    command = [harbor] if harbor else [sys.executable, "-m", "harbor.cli.main"]
    command += ["run", "-c", str(job_config), "-e", "podman"]
    if source is not None:
        mounts = [{
            "type": "bind",
            "source": str(source),
            "target": AUTH_TARGET,
            "read_only": True,
            "bind": {"create_host_path": False},
        }]
        command += [
            "--agent-env", f"XDG_DATA_HOME={XDG_DATA_HOME}",
            "--mounts", json.dumps(mounts, separators=(",", ":")),
        ]
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-config", type=Path, required=True)
    parser.add_argument(
        "--auth-json", default=os.environ.get(AUTH_ENV),
        help=f"local OpenCode auth.json (default: ${AUTH_ENV}; omit for no login mount)",
    )
    args = parser.parse_args(argv)
    try:
        command = harbor_command(args.job_config, args.auth_json)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        return subprocess.call(command)
    except OSError as exc:
        parser.error(f"could not start Harbor: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())

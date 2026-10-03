"""Harbor installed agent: copy OpenCode in during setup, start it with no network."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

# podman-compose wraps its external-provider banner in these.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _stdout(result) -> str:
    """Drop podman-compose's banner and the ANSI resets around it."""
    text = _ANSI.sub("", result.stdout or "")
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and "Executing external compose provider" not in line
    ]
    return "\n".join(lines)


class OpenCode(BaseInstalledAgent):
    """OpenCode payload copied from the host. run() does not call a model."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._payload = Path(__file__).resolve().parent

    @staticmethod
    def name() -> str:
        return "opencode"

    def _version_ref(self) -> str:
        ref = (self._payload / "version").read_text().strip()
        if not ref:
            raise RuntimeError("harness/opencode/version is empty")
        return ref

    def _ensure_config(self) -> Path:
        path = self._payload / "opencode.json"
        if not path.is_file():
            subprocess.run([str(self._payload / "render-config.sh")], check=True)
        if not path.is_file():
            raise RuntimeError("opencode.json was not rendered")
        return path

    async def install(self, environment: BaseEnvironment) -> None:
        payload = self._payload
        src = payload / "src"
        binary = payload / "bin" / "opencode"
        revision = payload / "REVISION"
        if not src.is_dir() or not binary.is_file() or not revision.is_file():
            raise RuntimeError(
                "OpenCode payload is missing. Run harness/opencode/pull.sh first."
            )
        if any(src.rglob(".git")):
            raise RuntimeError("OpenCode src still contains .git")

        config = self._ensure_config()
        await self.exec_as_root(environment, "mkdir -p /opt/opencode")
        await environment.upload_dir(src, "/opt/opencode")
        await environment.upload_file(binary, "/usr/local/bin/opencode")
        await environment.upload_file(revision, "/opt/opencode/REVISION")
        await environment.upload_file(config, "/opt/opencode/opencode.json")
        await self.exec_as_root(environment, "chmod 755 /usr/local/bin/opencode")

        git = await environment.exec(
            command="find /opt/opencode -name '.git' -print",
        )
        hits = _stdout(git)
        if hits:
            raise RuntimeError(f"git metadata was copied into /opt/opencode:\n{hits}")

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del instruction, context
        bare = self._version_ref().removeprefix("v")

        version = await self.exec_as_agent(
            environment,
            "opencode --version",
            timeout_sec=90,
        )
        printed = _stdout(version)
        if printed != bare:
            raise RuntimeError(f"opencode --version printed {printed!r}, want {bare}")

        config = await self.exec_as_agent(
            environment,
            "opencode debug config",
            env={"OPENCODE_CONFIG": "/opt/opencode/opencode.json"},
            timeout_sec=120,
        )
        raw = _stdout(config)
        start = raw.find("{")
        if start < 0:
            raise RuntimeError("opencode debug config did not print JSON")
        doc = json.loads(raw[start:])
        perm = doc.get("permission") or {}
        if perm.get("websearch") != "deny" or perm.get("webfetch") != "deny":
            raise RuntimeError(f"permission is {perm}")
        if "bash" in perm:
            raise RuntimeError("bash is set in the OpenCode permission object")

        # Harbor redirects TCP to a local proxy that accepts the handshake,
        # then drops the payload. A bare connect can succeed while no HTTP
        # response comes back. Require a status line to call it a leak.
        probe = await environment.exec(
            command=(
                "timeout 12 bash -c '"
                "exec 3<>/dev/tcp/example.com/80 || exit 2; "
                'printf "GET / HTTP/1.0\\r\\nHost: example.com\\r\\n\\r\\n" >&3 || exit 3; '
                "IFS= read -r -t 5 line <&3 || exit 4; "
                'case "$line" in HTTP/*) exit 0;; *) exit 5;; esac'
                "'"
            ),
            timeout_sec=20,
        )
        if probe.return_code == 0:
            raise RuntimeError(
                "agent phase got an HTTP response from example.com:\n"
                + _stdout(probe)
            )

        note = "\n".join(
            [
                f"version: {printed}",
                "websearch: deny",
                "webfetch: deny",
                "bash: absent",
                f"connect: failed {probe.return_code}",
                "",
            ]
        )
        (self.logs_dir / "opencode-stage.txt").write_text(note)
        print(note, end="")

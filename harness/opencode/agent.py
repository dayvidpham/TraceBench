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
        config = json.loads((self._payload.parent / "config.json").read_text())
        ref = config.get("version")
        if not isinstance(ref, str) or not ref:
            raise RuntimeError("harness/config.json version must be a non-empty string")
        return ref

    def _policy(self) -> tuple[dict, dict, dict]:
        """Load the external toggle and the per-module map, return expected perm.

        config.json is the single external config (version plus search/fetch).
        tool-map.json is contained per module (harness name -> OpenCode key).
        """
        allowed = {"search", "fetch"}
        config = json.loads((self._payload.parent / "config.json").read_text())
        if set(config) != {"tools", "version"}:
            raise RuntimeError("harness/config.json must contain only version and tools")
        tools = config.get("tools")
        mapping = json.loads((self._payload / "tool-map.json").read_text())
        if not isinstance(tools, dict):
            raise RuntimeError("harness/config.json tools must be an object")
        if set(tools) != allowed or set(mapping) != allowed:
            raise RuntimeError(f"policy must be exactly {sorted(allowed)}")
        for name, enabled in tools.items():
            if not isinstance(enabled, bool):
                raise RuntimeError(f"tool {name!r} must be bool")
        for tool in mapping.values():
            if tool == "bash":
                raise RuntimeError("refusing to map bash")
        expected = {mapping[name]: "deny" for name in sorted(allowed) if not tools[name]}
        if "bash" in expected:
            raise RuntimeError("refusing to deny bash")
        return tools, mapping, expected

    def _ensure_config(self) -> Path:
        path = self._payload / "opencode.json"
        # This output is never committed: render from the sole parent config
        # each time so no stale generated policy can be installed.
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
        tools, mapping, expected = self._policy()
        perm = doc.get("permission") or {}
        if perm != expected:
            raise RuntimeError(f"permission is {perm}, want {expected}")
        if "bash" in perm:
            raise RuntimeError("bash is set in the OpenCode permission object")

        # Explicit deny removes the tool before a prompt exists. --auto only
        # replies to permission.asked, and its help text refuses explicit denies.
        # Only search/fetch are toggleable; bash is never in the policy.
        for name in sorted(mapping):
            tool = mapping[name]
            built = await environment.exec(
                command=f"opencode debug agent build --tool {tool} --params '{{}}'",
                env={"OPENCODE_CONFIG": "/opt/opencode/opencode.json"},
                timeout_sec=90,
            )
            text = _stdout(built)
            if not tools[name]:
                if f"Tool {tool} is disabled" not in text:
                    raise RuntimeError(f"{tool} was not disabled:\n{text}")
            elif built.return_code != 0 or "is disabled" in text:
                raise RuntimeError(f"{tool} was not enabled:\n{text}")

        bash_tool = await environment.exec(
            command=(
                "opencode debug agent build --tool bash "
                """--params '{"command":"true"}'"""
            ),
            env={"OPENCODE_CONFIG": "/opt/opencode/opencode.json"},
            timeout_sec=90,
        )
        bash_text = _stdout(bash_tool)
        if bash_tool.return_code != 0 or "is disabled" in bash_text:
            raise RuntimeError(f"bash tool did not run:\n{bash_text}")

        help_text = await self.exec_as_agent(
            environment,
            "opencode run --help",
            timeout_sec=60,
        )
        if "not explicitly denied" not in _stdout(help_text):
            raise RuntimeError("opencode --auto does not say explicit denies stay denied")

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
                "webfetch-tool: disabled",
                "websearch-tool: disabled",
                "bash-tool: enabled",
                "auto: explicit deny holds",
                f"connect: failed {probe.return_code}",
                "",
            ]
        )
        (self.logs_dir / "opencode-stage.txt").write_text(note)
        print(note, end="")

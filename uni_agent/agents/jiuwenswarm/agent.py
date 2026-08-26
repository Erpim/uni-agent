"""jiuwenswarm: a black-box agent that runs the jiuwenswarm CLI inside the sandbox.

jiuwenswarm is launched *in* the sandbox from a prebuilt tool image (mounted at
``/opt/jiuwenswarm``) whose ``bin/run_agent.sh`` reads the task prompt from
**stdin** and the gateway config from **env vars**. This host-side agent encodes
the prompt via base64, pipes it in, and parses the resulting JSON out of stdout.

Reference: https://gitcode.com/openJiuwen/jiuwenswarm
"""

from __future__ import annotations

import base64
import json
import logging
import shlex
from typing import TYPE_CHECKING, Any

from pydantic import Field

from ..base import Agent, AgentConfig, AgentResult
from ..registry import register_agent

if TYPE_CHECKING:
    from uni_agent.sandbox import Sandbox

logger = logging.getLogger(__name__)


def build_agent_command(
    *,
    task_b64: str,
    conda_env: str = "testbed",
    tool_script: str,
    gateway_url: str,
    model_name: str = "openai/default",
    api_key: str = "EMPTY",
    project_dir: str = "/testbed",
) -> str:
    """Build the shell command that runs ``run_agent.sh`` inside the sandbox.

    The task prompt is piped via base64-encoded stdin. The gateway config
    (model endpoint, API key, project dir) is passed as env vars so
    ``run_agent.sh`` can write the ``.env`` file for ``jiuwenswarm-app``.
    The tool bash is called through the task's conda env so jiuwenswarm's
    tool subprocesses resolve the repo environment inside the project dir.
    """
    conda_prefix = f"/opt/miniconda3/envs/{conda_env}"
    run_agent_env = (
        f"CONDA_DEFAULT_ENV={shlex.quote(conda_env)} "
        f"CONDA_PREFIX={shlex.quote(conda_prefix)} "
        f"PATH={shlex.quote(conda_prefix + '/bin')}:/opt/miniconda3/bin:$PATH "
        f"JWS_API_BASE={shlex.quote(gateway_url)} "
        f"JWS_MODEL_NAME={shlex.quote(model_name)} "
        f"JWS_API_KEY={shlex.quote(api_key)} "
        f"JWS_PROJECT_DIR={shlex.quote(project_dir)} "
        "PIP_DISABLE_PIP_VERSION_CHECK=1 "
        "PIP_PROGRESS_BAR=off"
    )
    return (
        "unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy; "
        f"printf %s {shlex.quote(task_b64)} | base64 -d | "
        f"env {run_agent_env} bash {shlex.quote(tool_script)}"
    )


def parse_agent_result(stdout: str, exit_code: int) -> dict[str, Any]:
    """Parse the result JSON from ``run_agent.sh``'s stdout.

    ``exit_code == -1`` means the sandbox ``exec_shell`` timed out. Otherwise
    the last line starting with ``{`` wins (anti-noise). The jiuwenswarm CLI's
    ``--json`` output carries ``ok``, ``content``, and optionally ``error``;
    we inject ``exit_status`` from ``ok``.
    """
    if exit_code == -1:
        return {"exit_status": "timeout", "ok": False, "content": "", "error": "agent process timed out"}
    stdout = stdout.strip()
    if not stdout:
        return {"exit_status": "error", "ok": False, "content": "", "error": "empty stdout"}
    for line in reversed([ln.strip() for ln in stdout.split("\n") if ln.strip()]):
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
                if "ok" in parsed:
                    parsed.setdefault("exit_status", "ok" if parsed.get("ok") else "error")
                    return parsed
            except json.JSONDecodeError:
                continue
    try:
        parsed = json.loads(stdout)
        if "ok" in parsed:
            parsed.setdefault("exit_status", "ok" if parsed.get("ok") else "error")
            return parsed
    except json.JSONDecodeError:
        logger.warning("jiuwenswarm: failed to parse agent result (stdout tail): %.1000s", stdout)
    return {"exit_status": "error", "ok": False, "content": "", "error": "unparseable stdout"}


class JiuwenswarmConfig(AgentConfig):
    """Black-box launch params for jiuwenswarm (endpoint lives on :attr:`AgentConfig.model`)."""

    name: str = "jiuwenswarm"
    run_timeout: float = Field(default=7200.0, description="Wallclock cap (s) on the agent process.")
    conda_env: str = Field(default="testbed", description="Task repo conda env, activated around the launch.")
    # Tool-image path is bound to the prebuilt tool image's Dockerfile layout
    # (mounted at /opt/jiuwenswarm), so it is required here and declared by
    # the recipe's task config instead of being hardcoded as a default.
    tool_script: str = Field(
        description="In-sandbox entrypoint path (e.g. /opt/jiuwenswarm/bin/run_agent.sh)."
    )


@register_agent("jiuwenswarm")
class JiuwenswarmAgent(Agent):
    """Black-box solver: run jiuwenswarm in the sandbox against ``config.model``."""

    config_model = JiuwenswarmConfig

    async def run(
        self,
        *,
        sandbox: Sandbox,
        messages: list[dict[str, Any]],
        workdir: str | None = None,
    ) -> AgentResult:
        cfg: JiuwenswarmConfig = self.config  # type: ignore[assignment]
        if cfg.model.base_url is None:
            raise ValueError("jiuwenswarm: config.model.base_url is not set (the gateway/vLLM policy endpoint)")
        task = self._extract_task(messages)
        project_dir = workdir or "/testbed"

        # 1) Base64-encode the task prompt. The agent is tunnel-agnostic: when
        #    a reverse tunnel is configured, run_task has already rewritten
        #    model.base_url to http://127.0.0.1:<proxy_port>, so it passes
        #    through as-is.
        task_b64 = base64.b64encode(task.encode()).decode()

        # 2) Run the in-sandbox bash entrypoint (task via stdin, config via env).
        agent_cmd = build_agent_command(
            task_b64=task_b64,
            conda_env=cfg.conda_env,
            tool_script=cfg.tool_script,
            gateway_url=cfg.model.base_url,
            model_name=cfg.model.model_name or "openai/default",
            api_key=cfg.model.api_key or "EMPTY",
            project_dir=project_dir,
        )
        result = await sandbox.exec_shell(agent_cmd, timeout=cfg.run_timeout, workdir=project_dir)

        # 3) Parse the result JSON from stdout (exit_code -1 == timeout).
        agent_info = parse_agent_result(result.stdout or "", result.exit_code)
        logger.info(
            "jiuwenswarm: done exit_status=%s ok=%s exit_code=%s",
            agent_info.get("exit_status"),
            agent_info.get("ok"),
            result.exit_code,
        )
        return AgentResult(
            output=agent_info,
            transcript=list(messages),
            info={
                "exit_status": agent_info.get("exit_status"),
                "ok": agent_info.get("ok"),
            },
            # finished = ok == true: the jiuwenswarm CLI completed normally
            # and produced a valid result. Anything else (error, timeout) is
            # "not finished", so those episodes can be masked from the loss
            # via mask_unfinished_episode=True in the framework config.
            finished=agent_info.get("ok") is True,
        )

    @staticmethod
    def _extract_task(messages: list[dict[str, Any]]) -> str:
        if len(messages) > 2:
            raise ValueError(f"jiuwenswarm accepts at most 2 messages (system?, user), got {len(messages)}")
        problem = next((m["content"] for m in messages if m.get("role") == "user"), None)
        if not problem:
            raise ValueError("jiuwenswarm requires a 'user' message (the problem statement)")
        return problem
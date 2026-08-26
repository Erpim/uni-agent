# Jiuwenswarm Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the jiuwenswarm agent (PyPI: `workswarm==0.2.5`) into uni-agent as a sidecar-tool agent for SWE-bench code repair, following the mini_swe_agent pattern.

**Architecture:** Sidecar `FROM scratch` tool image (Dockerfile.jiuwenswarm-tool) mounted at `/opt/jiuwenswarm` inside an openyuanrong sandbox; `run_agent.sh` as the in-sandbox bash entrypoint that reads the task prompt from **stdin** (host pipes base64) and the gateway config from **env vars** (`JWS_API_BASE`/`JWS_MODEL_NAME`/`JWS_API_KEY`/`JWS_PROJECT_DIR`); host-side `JiuwenswarmAgent` class encodes the task, pipes it in, and parses the result JSON. Reuses existing `swe_bench` task and `compute_reward`.

> **Note:** This plan was originally written for a JSON-file handoff (`/tmp/jiuwenswarm_task.json`). Per the user's approval, the implementation was simplified to **stdin + env vars** (zero Python in `run_agent.sh`, CLI `--json` output passed through, `exit_status` derived host-side). The step descriptions below reflect the shipped approach.

**Tech Stack:** uni-agent, openyuanrong sandbox, python-build-standalone 3.12, workswarm==0.2.5, jiuwenswarm code.normal mode, bash, pytest

---

### Task 1: Sidecar tool image (Dockerfile)

**Files:**
- Create: `examples/jiuwenswarm/Dockerfile.jiuwenswarm-tool`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# Jiuwenswarm sidecar tool image.
#
# Contains a self-contained Python runtime at /opt/jiuwenswarm with
# workswarm (jiuwenswarm) + all dependencies. When mounted into a sandbox
# at /opt/jiuwenswarm, the agent can be invoked via:
#
#   bash /opt/jiuwenswarm/bin/run_agent.sh   # prompt from stdin, config from env
#
# Uses python-build-standalone for maximum portability across different
# glibc versions (built against older glibc, forward-compatible).
#
# Build:
#   docker build -f Dockerfile.jiuwenswarm-tool -t jiuwenswarm-tool:latest .
#
# The openjiuwen dependency is a git dep from gitcode.com, so the builder
# stage needs git + network access.

FROM debian:bullseye-slim AS builder

ARG PBS_RELEASE="20260602"
ARG PBS_PYTHON="3.12.13"
ARG PIP_INDEX_URL=""

# Install git + build deps for openjiuwen (git dependency from gitcode.com)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates wget git \
    && rm -rf /var/lib/apt/lists/*

# Download and extract python-build-standalone (stripped, 32MB)
RUN wget -q \
    "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/cpython-${PBS_PYTHON}%2B${PBS_RELEASE}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz" \
    -O /tmp/python.tar.gz \
    && mkdir -p /opt/jiuwenswarm \
    && tar -xzf /tmp/python.tar.gz -C /opt/jiuwenswarm --strip-components=1 \
    && rm /tmp/python.tar.gz

# Install workswarm (jiuwenswarm) + all dependencies (incl. openjiuwen from git)
RUN /opt/jiuwenswarm/bin/pip install --no-cache-dir \
    ${PIP_INDEX_URL:+-i ${PIP_INDEX_URL}} \
    "workswarm==0.2.5"

# Copy the in-sandbox runner script
COPY run_agent.sh /opt/jiuwenswarm/bin/run_agent.sh

# Final scratch image: files are at the image root level so that when
# akernel_sdk.Mount(target="/opt/jiuwenswarm") overlays this image,
# the files appear at /opt/jiuwenswarm/bin/run_agent.sh etc.
FROM scratch
COPY --from=builder /opt/jiuwenswarm /
```

- [ ] **Step 2: Build the image locally**

```bash
cd /home/dyp/recipe/uni-agent/examples/jiuwenswarm
docker build -f Dockerfile.jiuwenswarm-tool -t jiuwenswarm-tool:latest .
```

Expected: Image builds successfully, exits with `DONE` and image ID.

---

### Task 2: Sidecar build/push script

**Files:**
- Create: `examples/jiuwenswarm/build_tool.sh`

- [ ] **Step 1: Write the build script**

```bash
#!/usr/bin/env bash
# Build the jiuwenswarm sidecar tool image.
#
# The image uses python-build-standalone to build an isolated Python runtime
# with workswarm + run_agent.sh, copied into a minimal `FROM scratch` final
# stage rooted at /opt/jiuwenswarm. It is mounted into the SWE-bench sandbox
# at /opt/jiuwenswarm, so the sandbox base image does not need Python for
# the sidecar tool runtime.
#
# Usage:
#   bash examples/jiuwenswarm/build_tool.sh
#   bash examples/jiuwenswarm/build_tool.sh --pip-index https://pypi.tuna.tsinghua.edu.cn/simple/
#   bash examples/jiuwenswarm/build_tool.sh --registry swr.cn-east-3.myhuaweicloud.com/openyuanrong
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${TOOL_IMAGE:-jiuwenswarm-tool}"
IMAGE_TAG="${TOOL_TAG:-latest}"

# Parse args
REGISTRY=""
PIP_INDEX_URL="${PIP_INDEX_URL:-}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --registry) REGISTRY="$2"; shift 2 ;;
        --pip-index) PIP_INDEX_URL="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

BUILD_ARGS=()
if [[ -n "${PIP_INDEX_URL}" ]]; then
    BUILD_ARGS+=(--build-arg PIP_INDEX_URL="${PIP_INDEX_URL}")
fi

echo "==> Building jiuwenswarm tool image: ${IMAGE_NAME}:${IMAGE_TAG}"
docker build \
    -f "${SCRIPT_DIR}/Dockerfile.jiuwenswarm-tool" \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    "${BUILD_ARGS[@]}" \
    "${SCRIPT_DIR}/"

if [[ -n "${REGISTRY}" ]]; then
    FULL_TAG="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
    echo "==> Tagging and pushing: ${FULL_TAG}"
    docker tag "${IMAGE_NAME}:${IMAGE_TAG}" "${FULL_TAG}"
    docker push "${FULL_TAG}"
    echo "    Pushed."
fi

echo ""
echo "Tool image ready: ${IMAGE_NAME}:${IMAGE_TAG}"
if [[ -n "${REGISTRY}" ]]; then
    echo "  Remote sandbox: ${FULL_TAG}"
fi
```

- [ ] **Step 2: Make executable and verify**

```bash
chmod +x /home/dyp/recipe/uni-agent/examples/jiuwenswarm/build_tool.sh
```

---

### Task 3: run_agent.sh (in-sandbox entry)

**Files:**
- Modify: `examples/jiuwenswarm/run_agent.sh` (rewrite as in-sandbox entry)

- [ ] **Step 1: Write the rewritten run_agent.sh**

```bash
#!/usr/bin/env bash
# Env vars (set by the host agent's build_agent_command):
#   JWS_API_BASE      — gateway URL
#   JWS_MODEL_NAME    — model name (default: openai/default)
#   JWS_API_KEY       — API key (default: EMPTY)
#   JWS_PROJECT_DIR   — repo working dir (host's workdir, e.g. /testbed); falls back to $PWD
set -uo pipefail
TOOL_BIN="/opt/jiuwenswarm/bin"
export PATH="${PATH}:${TOOL_BIN}"
JWS_WS="${JIUWENSWARM_DATA_DIR:-$HOME/.jiuwenswarm}"
ENV_FILE="$JWS_WS/config/.env"
GATEWAY_PORT="${GATEWAY_PORT:-19001}"
TASK="$(cat)"                       # prompt from stdin
: "${JWS_API_BASE:?missing gateway_url}"
PROJECT_DIR="${JWS_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR"
echo "" | jiuwenswarm-init >/dev/null 2>&1 || true
mkdir -p "$JWS_WS/logs"
cat > "$ENV_FILE" <<EOF
MODEL_PROVIDER=OpenAI
API_BASE=${JWS_API_BASE}
MODEL_NAME=${JWS_MODEL_NAME:-openai/default}
API_KEY=${JWS_API_KEY:-EMPTY}
EOF
if ! (exec 3<>"/dev/tcp/127.0.0.1/$GATEWAY_PORT") 2>/dev/null; then
  nohup jiuwenswarm-app >"$JWS_WS/logs/startup.log" 2>&1 &
  for i in $(seq 1 180); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$GATEWAY_PORT") 2>/dev/null; then
      echo "[ok] gateway ready on :$GATEWAY_PORT" >&2
      break
    fi
    [ "$i" -eq 180 ] && { echo "[error] gateway timeout" >&2; exit 3; }
    sleep 1
  done
fi
printf '%s' "$TASK" | jiuwenswarm --mode code.normal --json \
  --cwd "$PROJECT_DIR" --trusted-dir "$PROJECT_DIR"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x /home/dyp/recipe/uni-agent/examples/jiuwenswarm/run_agent.sh
```

---

### Task 4: Host agent package (__init__.py + agent.py)

**Files:**
- Create: `uni_agent/agents/jiuwenswarm/__init__.py`
- Create: `uni_agent/agents/jiuwenswarm/agent.py`

- [ ] **Step 1: Write __init__.py**

```python
"""jiuwenswarm agent (black-box: launched inside the sandbox with its own model)."""

from __future__ import annotations

from .agent import JiuwenswarmAgent, JiuwenswarmConfig

__all__ = ["JiuwenswarmAgent", "JiuwenswarmConfig"]
```

- [ ] **Step 2: Write agent.py**

```python
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
```

---

### Task 5: Register in registry.py

**Files:**
- Modify: `uni_agent/agents/registry.py`

- [ ] **Step 1: Add jiuwenswarm module to AGENT_MODULES**

```python
    "jiuwenswarm": "uni_agent.agents.jiuwenswarm.agent",
```

Insert after the `"mini_swe_agent"` line so the dict reads:

```python
AGENT_MODULES: dict[str, str] = {
    "react": "uni_agent.agents.react.agent",
    "claude_code": "uni_agent.agents.claude_code.agent",
    "mini_swe_agent": "uni_agent.agents.mini_swe_agent.agent",
    "jiuwenswarm": "uni_agent.agents.jiuwenswarm.agent",
    "mem_agent": "uni_agent.agents.mem_agent.agent",
}
```

---

### Task 6: Tests for host agent glue

**Files:**
- Create: `tests/uni_agent/agents/test_jiuwenswarm_agent.py`

- [ ] **Step 1: Write the test file**

```python
"""Tests for the jiuwenswarm agent's host-side glue.

jiuwenswarm runs entirely *inside* the sandbox from a prebuilt tool image
mounted at ``/opt/jiuwenswarm``. The agent's host-side glue is thin: base64-
encode the task prompt, pipe it via stdin, pass the gateway config (endpoint,
API key, project dir) as env vars, and parse the result JSON out of stdout.
These tests cover that glue with a tiny in-memory fake sandbox, so they run
fast under ``pytest``.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from uni_agent.agents.base import AgentResult, ModelConfig
from uni_agent.agents.jiuwenswarm.agent import (
    JiuwenswarmAgent,
    JiuwenswarmConfig,
    build_agent_command,
    parse_agent_result,
)
from uni_agent.sandbox.base import ExecResult


class _FakeSandbox:
    """Records the one ``exec_shell`` call and returns canned stdout."""

    def __init__(self, *, stdout: str = "", exit_code: int = 0):
        self._stdout = stdout
        self._exit_code = exit_code
        self.exec_shell_calls: list[str] = []

    async def exec_shell(self, script, *, timeout=None, workdir=None, env=None):
        self.exec_shell_calls.append(script)
        return ExecResult(exit_code=self._exit_code, stdout=self._stdout, stderr="")


_TOOL_SCRIPT = "/opt/jiuwenswarm/bin/run_agent.sh"


def _agent(base_url: str = "http://gateway:8000/v1", **config_kwargs) -> JiuwenswarmAgent:
    model = ModelConfig(base_url=base_url, model_name="policy")
    config_kwargs.setdefault("tool_script", _TOOL_SCRIPT)
    return JiuwenswarmAgent(JiuwenswarmConfig(model=model, **config_kwargs))


def _decode_task_b64(cmd: str) -> str:
    """Pull the base64-encoded task out of a built agent command and decode it."""
    _, _, after = cmd.partition("printf %s ")
    task_b64, _, _ = after.partition(" | base64 -d")
    return base64.b64decode(task_b64).decode()


# --------------------------- build_agent_command ---------------------------


def test_build_agent_command_pipes_task_and_sets_env():
    cmd = build_agent_command(
        task_b64=base64.b64encode(b"fix the bug").decode(),
        conda_env="testbed",
        tool_script=_TOOL_SCRIPT,
        gateway_url="http://127.0.0.1:38197/sessions/abc/v1",
        model_name="policy",
        api_key="EMPTY",
        project_dir="/testbed",
    )
    assert cmd.startswith("unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy;")
    assert "| base64 -d |" in cmd
    assert "/opt/jiuwenswarm/bin/run_agent.sh" in cmd
    # The task conda env is activated around the launch.
    assert "CONDA_DEFAULT_ENV=testbed" in cmd
    assert "/opt/miniconda3/envs/testbed/bin" in cmd
    # Gateway config rides env vars (not argv) so long prompts stay on stdin.
    assert "JWS_API_BASE=http://127.0.0.1:38197/sessions/abc/v1" in cmd
    assert "JWS_MODEL_NAME=policy" in cmd
    assert "JWS_API_KEY=EMPTY" in cmd
    assert "JWS_PROJECT_DIR=/testbed" in cmd


def test_build_agent_command_honors_overrides():
    cmd = build_agent_command(
        task_b64="",
        conda_env="myenv",
        tool_script="/x/run.sh",
        gateway_url="http://g/v1",
        project_dir="/work",
    )
    assert "/x/run.sh" in cmd
    assert "CONDA_DEFAULT_ENV=myenv" in cmd
    assert "JWS_PROJECT_DIR=/work" in cmd


# --------------------------- parse_agent_result ---------------------------


def test_parse_agent_result_timeout():
    assert parse_agent_result("", -1) == {
        "exit_status": "timeout", "ok": False, "content": "", "error": "agent process timed out"
    }


def test_parse_agent_result_empty_is_error():
    assert parse_agent_result("", 0) == {"exit_status": "error", "ok": False, "content": "", "error": "empty stdout"}


def test_parse_agent_result_injects_exit_status_from_ok():
    assert parse_agent_result(json.dumps({"ok": True, "content": "fixed it"}), 0) == {
        "exit_status": "ok", "ok": True, "content": "fixed it"
    }
    assert parse_agent_result(json.dumps({"ok": False, "error": "conn failed"}), 1) == {
        "exit_status": "error", "ok": False, "error": "conn failed"
    }


def test_parse_agent_result_picks_last_json_line_ignoring_noise():
    stdout = "some noise\n" + json.dumps({"ok": True, "content": "done"})
    assert parse_agent_result(stdout, 0) == {"exit_status": "ok", "ok": True, "content": "done"}


def test_parse_agent_result_unparseable_is_error():
    assert parse_agent_result("totally not json", 0) == {
        "exit_status": "error", "ok": False, "content": "", "error": "unparseable stdout"
    }


# --------------------------- validation ---------------------------


def test_missing_base_url_raises():
    agent = JiuwenswarmAgent(JiuwenswarmConfig(tool_script=_TOOL_SCRIPT))
    with pytest.raises(ValueError, match="base_url"):
        asyncio.run(agent.run(sandbox=_FakeSandbox(), messages=[{"role": "user", "content": "fix the bug"}]))


def test_missing_user_message_raises():
    agent = _agent()
    with pytest.raises(ValueError, match="requires a 'user' message"):
        asyncio.run(agent.run(sandbox=_FakeSandbox(), messages=[{"role": "system", "content": "sys"}]))


def test_too_many_messages_raises():
    agent = _agent()
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}, {"role": "user", "content": "u2"}]
    with pytest.raises(ValueError, match="at most 2 messages"):
        asyncio.run(agent.run(sandbox=_FakeSandbox(), messages=messages))


# --------------------------- happy path ---------------------------


def test_run_pipes_task_and_parses_stdout_into_result():
    stdout = json.dumps({"ok": True, "content": "fixed the off-by-one"})
    sandbox = _FakeSandbox(stdout=stdout)
    agent = _agent()
    messages = [{"role": "system", "content": "be careful"}, {"role": "user", "content": "fix the off-by-one bug"}]

    result = asyncio.run(agent.run(sandbox=sandbox, messages=messages))

    # Exactly one exec_shell, piping the base64 task into the tool bash script.
    assert len(sandbox.exec_shell_calls) == 1
    cmd = sandbox.exec_shell_calls[0]
    assert "| base64 -d |" in cmd
    assert "/opt/jiuwenswarm/bin/run_agent.sh" in cmd

    # The decoded task is the user prompt; gateway config rides env vars.
    assert _decode_task_b64(cmd) == "fix the off-by-one bug"
    assert "JWS_API_BASE=http://gateway:8000/v1" in cmd
    assert "JWS_MODEL_NAME=policy" in cmd
    assert "JWS_PROJECT_DIR=/testbed" in cmd

    # The result is parsed out of stdout into an AgentResult.
    assert isinstance(result, AgentResult)
    assert result.output["exit_status"] == "ok"
    assert result.output["ok"] is True
    assert result.output["content"] == "fixed the off-by-one"
    assert result.info == {"exit_status": "ok", "ok": True}
    assert result.finished is True
    assert result.transcript == messages


def test_run_marks_unfinished_when_not_ok():
    sandbox = _FakeSandbox(stdout=json.dumps({"ok": False, "error": "connection failed"}))
    agent = _agent()
    result = asyncio.run(agent.run(sandbox=sandbox, messages=[{"role": "user", "content": "task"}]))
    assert result.finished is False
    assert result.info["ok"] is False
    assert result.info["exit_status"] == "error"


def test_run_marks_unfinished_on_timeout():
    sandbox = _FakeSandbox(stdout="", exit_code=-1)
    agent = _agent()
    result = asyncio.run(agent.run(sandbox=sandbox, messages=[{"role": "user", "content": "task"}]))
    assert result.finished is False
    assert result.info["exit_status"] == "timeout"
    assert result.output["error"] == "agent process timed out"


def test_run_passes_base_url_through_unchanged():
    sandbox = _FakeSandbox(stdout=json.dumps({"ok": True, "content": "done"}))
    # A tunnel-rewritten base_url (what run_task injects) is forwarded verbatim.
    agent = _agent(base_url="http://127.0.0.1:38197/sessions/abc/v1")
    asyncio.run(agent.run(sandbox=sandbox, messages=[{"role": "user", "content": "task"}]))
    cmd = sandbox.exec_shell_calls[0]
    assert "JWS_API_BASE=http://127.0.0.1:38197/sessions/abc/v1" in cmd


def test_run_uses_external_workdir_as_project_dir():
    sandbox = _FakeSandbox(stdout=json.dumps({"ok": True, "content": "done"}))
    agent = _agent()
    asyncio.run(agent.run(sandbox=sandbox, messages=[{"role": "user", "content": "task"}], workdir="/repo"))
    cmd = sandbox.exec_shell_calls[0]
    assert "JWS_PROJECT_DIR=/repo" in cmd


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd /home/dyp/recipe/uni-agent
python -m pytest tests/uni_agent/agents/test_jiuwenswarm_agent.py -v
```

Expected: 15 tests PASS, 0 failures.

---

### Task 7: Task config YAML

**Files:**
- Create: `examples/jiuwenswarm/task_config_jiuwenswarm.yaml`

- [ ] **Step 1: Write the task config**

```yaml
# Task Config for the jiuwenswarm blackbox recipe.
#
# The sample rows (produced by `uni_agent.tasks.swe_bench.preprocess`) carry
# only the canonical sandbox image name (`sandbox.image`) plus prompt/metadata;
# everything below is the per-task default layered on top by `TaskConfigResolver`.
#
# - sandbox.provider=openyuanrong reuses `uni_agent.sandbox.openyuanrong`; the
#   sandbox mounts the prebuilt tool image (workswarm + run_agent.sh) at
#   /opt/jiuwenswarm.
# - sandbox.sandbox_kwargs.proxy_port is the in-sandbox reverse-tunnel port; the
#   runtime `upstream` (gateway host:port) is injected by `run_task`, which also
#   rewrites the agent's model.base_url to http://127.0.0.1:<proxy_port> so the
#   agent itself stays tunnel-agnostic.
# - agent.model.base_url/api_key/model_name are injected at runtime from the
#   gateway session; run_task rewrites base_url to the tunnel address when a
#   tunnel is configured (openyuanrong sandboxes only).
# - agent.tool_script is REQUIRED: it names the path inside the prebuilt tool
#   image (mounted at /opt/jiuwenswarm); change it together with the tool image.
- name: swe_bench
  sandbox:
    provider: openyuanrong
    runtime_timeout: 7200
    image_map:
      - from: "swebench/**"
        to: "swr.cn-east-3.myhuaweicloud.com/openyuanrong/swe-bench-verified/**:v2"
    sandbox_kwargs:
      proxy_port: 38197
      mounts:
        - target: /opt/jiuwenswarm
          image_url: <registry>/jiuwenswarm-tool:latest
  agent:
    name: jiuwenswarm
    run_timeout: 7200
    conda_env: testbed
    tool_script: /opt/jiuwenswarm/bin/run_agent.sh
```

---

### Task 8: run_train.sh (RL training entry)

**Files:**
- Create: `examples/jiuwenswarm/run_train.sh`

- [ ] **Step 1: Copy from mini_swe_agent and adapt**

Copy `examples/mini_swe_agent/run_train.sh` to `examples/jiuwenswarm/run_train.sh`, then make these changes:

1. Change `TASK_CONFIG`:
   ```bash
   TASK_CONFIG="${TASK_CONFIG:-examples/jiuwenswarm/task_config_jiuwenswarm.yaml}"
   ```

2. Change the title banner:
   ```bash
   echo "=== Jiuwenswarm Blackbox Megatron Async Training ==="
   ```

3. Change the comment at the top regarding tool image:
   ```
   # jiuwenswarm runs *inside* the sandbox from a prebuilt tool image (mounted
   # at /opt/jiuwenswarm) and talks to the policy gateway through a reverse
   # tunnel.
   ```

4. Change `SANDBOX_NAME_PREFIX`:
   ```bash
   SANDBOX_NAME_PREFIX="${SANDBOX_NAME_PREFIX:-jiuwenswarm-}"
   ```

5. Change `PROJECT_NAME` / `EXPERIMENT_NAME`:
   ```bash
   PROJECT_NAME="${PROJECT_NAME:-jiuwenswarm_blackbox}"
   EXPERIMENT_NAME="${EXPERIMENT_NAME:-jiuwenswarm_$(date +%Y%m%d_%H%M)}"
   ```

6. The `TOOL_PARSER` and `MASK_UNFINISHED_EPISODE` remain the same: `TOOL_PARSER="${TOOL_PARSER:-qwen3_coder}"` and `MASK_UNFINISHED_EPISODE="${MASK_UNFINISHED_EPISODE:-True}"`.

All other lines (Ray launch, Megatron parallelism, gateway config, agent runner args) unchanged.

---

### Task 9: README

**Files:**
- Create: `examples/jiuwenswarm/README.md`

- [ ] **Step 1: Write the README**

```markdown
# jiuwenswarm Blackbox Sidecar Integration

Integrates the [jiuwenswarm](https://gitcode.com/openJiuwen/jiuwenswarm) agent
(PyPI: `workswarm==0.2.5`) into uni-agent as a sidecar tool image for SWE-bench
code repair tasks.

## Architecture

- **Tool image**: `Dockerfile.jiuwenswarm-tool` builds a `FROM scratch` image
  with python-build-standalone + `workswarm` + `run_agent.sh` entrypoint.
- **Sandbox**: openyuanrong remote sandbox with the tool image mounted at
  `/opt/jiuwenswarm`.
- **Host agent**: `uni_agent/agents/jiuwenswarm/JiuwenswarmAgent` base64-pipes
  the task prompt into the bash entrypoint, passes the gateway config via env
  vars, and parses the result JSON.
- **Task config**: `task_config_jiuwenswarm.yaml` declares sandbox mounts and
  agent params for the `swe_bench` task.
- **Training**: `run_train.sh` launches Megatron async PPO training.

## Build

```bash
# Build the tool image
bash examples/jiuwenswarm/build_tool.sh

# Build with a custom pip index (e.g., mirror)
bash examples/jiuwenswarm/build_tool.sh --pip-index https://pypi.tuna.tsinghua.edu.cn/simple/

# Build, tag and push to a registry
bash examples/jiuwenswarm/build_tool.sh --registry swr.cn-east-3.myhuaweicloud.com/openyuanrong
```

## Run (standalone evaluator)

```bash
python -m uni_agent.framework.task_runner \
  --task_config examples/jiuwenswarm/task_config_jiuwenswarm.yaml \
  --sample '{"instance_id": "django__django-12345", "image": "swebench/sweb.eval.x86_64.django__django-12345", ...}'
```

## Training

```bash
bash examples/jiuwenswarm/run_train.sh
```

## Files

| File | Purpose |
|---|---|
| `Dockerfile.jiuwenswarm-tool` | Sidecar tool image build |
| `build_tool.sh` | Build/push helper |
| `run_agent.sh` | In-sandbox bash entrypoint |
| `task_config_jiuwenswarm.yaml` | Task + sandbox defaults |
| `run_train.sh` | Megatron RL training launch |
| `DESIGN.md` | Design document |
| `README.md` | This file |

## Key differences from mini_swe_agent

- Uses **bash entrypoint** (`run_agent.sh`) instead of Python (`run_agent.py`)
- Task prompt piped via **stdin (base64)**; gateway config passed via **env vars**
  (`JWS_API_BASE` / `JWS_MODEL_NAME` / `JWS_API_KEY` / `JWS_PROJECT_DIR`)
- `finished = ok == true` (from jiuwenswarm's `--json` output), not
  `exit_status == "Submitted"`
- Runtime config of max_iterations / subagents is **not supported** in v1
  (uses jiuwenswarm defaults: `react.max_iterations: 100`)
```

---

### Task 10: Verification

- [ ] **Step 1: Run all unit tests**

```bash
cd /home/dyp/recipe/uni-agent
python -m pytest tests/uni_agent/agents/test_jiuwenswarm_agent.py tests/uni_agent/agents/test_mini_swe_agent_agent.py -v
```

Expected: All tests pass (new + existing mini_swe_agent tests unaffected).

- [ ] **Step 2: Verify registry loads correctly**

```bash
cd /home/dyp/recipe/uni-agent
python -c "
from uni_agent.agents.registry import get_agent_cls, build_agent
from uni_agent.agents.jiuwenswarm import JiuwenswarmConfig
cls = get_agent_cls('jiuwenswarm')
print('Agent class:', cls)
cfg = JiuwenswarmConfig(tool_script='/opt/jiuwenswarm/bin/run_agent.sh')
agent = build_agent(cfg)
print('Agent instance:', agent)
print('OK: jiuwenswarm agent registered and buildable')
"
```

Expected: Prints "jiuwenswarm agent registered and buildable" without errors.

- [ ] **Step 3: Verify all 9 files exist and are non-empty**

```bash
for f in \
  examples/jiuwenswarm/Dockerfile.jiuwenswarm-tool \
  examples/jiuwenswarm/build_tool.sh \
  examples/jiuwenswarm/run_agent.sh \
  uni_agent/agents/jiuwenswarm/__init__.py \
  uni_agent/agents/jiuwenswarm/agent.py \
  examples/jiuwenswarm/task_config_jiuwenswarm.yaml \
  examples/jiuwenswarm/run_train.sh \
  tests/uni_agent/agents/test_jiuwenswarm_agent.py \
  examples/jiuwenswarm/README.md
do
  [ -s "$f" ] && echo "OK: $f" || echo "MISSING/EMPTY: $f"
done
```

Expected: All 9 files shown as OK.
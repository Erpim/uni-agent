"""Tests for the mini-swe-agent agent's host-side glue.

mini-swe-agent runs entirely *inside* the sandbox (like claude_code): the agent
builds a task config (gateway URL rewritten to the sandbox-internal tunnel),
pipes it via base64 stdin into the prebuilt tool-image ``run_agent.py``, and
parses the result JSON out of stdout. ``_FakeSandbox`` is a tiny in-memory fake,
so this runs fast under ``pytest`` (or ``python`` on this file).
"""

from __future__ import annotations

import asyncio
import base64
import json
import re

import pytest

from uni_agent.agents.base import AgentResult, ModelConfig
from uni_agent.agents.mini_swe_agent.agent import MiniSweAgentAgent, MiniSweAgentConfig
from uni_agent.sandbox.base import ExecResult


class _FakeSandbox:
    """Records every ``exec_shell`` call and answers with canned stdout."""

    def __init__(self, stdout: str | None = None):
        default_result = {
            "exit_status": "Submitted",
            "submission": "diff --git a/test.py b/test.py\n",
            "model_stats": {"api_calls": 3, "instance_cost": 0.1},
        }
        self.stdout = stdout if stdout is not None else json.dumps(default_result) + "\n"
        self.exec_shell_calls: list[dict] = []

    async def exec_shell(self, script, *, timeout=None, workdir=None, env=None):
        self.exec_shell_calls.append({"script": script, "timeout": timeout, "workdir": workdir, "env": env})
        return ExecResult(exit_code=0, stdout=self.stdout, stderr="")


def _agent(**config_kwargs) -> MiniSweAgentAgent:
    model = ModelConfig(base_url="http://gateway:8000/v1")
    return MiniSweAgentAgent(MiniSweAgentConfig(model=model, **config_kwargs))


def _config_from_command(cmd: str) -> dict:
    """Extract and decode the base64 task config embedded in a launch command."""
    m = re.search(r"printf %s (\S+)", cmd)
    assert m, f"no base64 config found in command: {cmd}"
    token = m.group(1).strip("'\"")  # shlex.quote only quotes when needed
    return json.loads(base64.b64decode(token))


# --------------------------- validation ---------------------------


def test_missing_base_url_raises():
    agent = MiniSweAgentAgent(MiniSweAgentConfig())
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


def test_run_pipes_config_and_parses_stdout():
    sandbox = _FakeSandbox()
    agent = _agent(step_limit=25)
    messages = [{"role": "system", "content": "be careful"}, {"role": "user", "content": "fix the off-by-one bug"}]

    result = asyncio.run(agent.run(sandbox=sandbox, messages=messages))

    # exactly one exec_shell: the launch command
    assert len(sandbox.exec_shell_calls) == 1
    cmd = sandbox.exec_shell_calls[0]["script"]
    assert "base64 -d" in cmd
    assert "/opt/mini-swe-agent/bin/python" in cmd
    assert "run_agent.py" in cmd

    # the config piped in carries the rewritten tunnel URL + step_limit
    task_config = _config_from_command(cmd)
    assert task_config["task"] == "fix the off-by-one bug"
    assert task_config["gateway_url"] == "http://127.0.0.1:38197/v1"
    assert task_config["agent"]["step_limit"] == 25

    # the result comes from stdout, not the process exit code
    assert isinstance(result, AgentResult)
    assert result.output["exit_status"] == "Submitted"
    assert result.output["submission"].startswith("diff --git")
    assert result.info["step_limit"] == 25
    assert result.finished is True  # "Submitted" -> explicit finished


def test_default_step_limit_used_when_unset():
    sandbox = _FakeSandbox()
    agent = _agent()
    asyncio.run(agent.run(sandbox=sandbox, messages=[{"role": "user", "content": "task"}]))
    task_config = _config_from_command(sandbox.exec_shell_calls[0]["script"])
    assert task_config["agent"]["step_limit"] == 100


def test_custom_proxy_port_used_in_rewrite():
    sandbox = _FakeSandbox()
    agent = _agent(proxy_port=50000)
    asyncio.run(agent.run(sandbox=sandbox, messages=[{"role": "user", "content": "task"}]))
    task_config = _config_from_command(sandbox.exec_shell_calls[0]["script"])
    assert task_config["gateway_url"] == "http://127.0.0.1:50000/v1"


def test_run_timeout_forwarded_to_sandbox():
    sandbox = _FakeSandbox()
    agent = _agent(run_timeout=123.0)
    asyncio.run(agent.run(sandbox=sandbox, messages=[{"role": "user", "content": "task"}]))
    assert sandbox.exec_shell_calls[0]["timeout"] == 123.0


# --------------------------- stdout resilience ---------------------------


def test_polluted_stdout_picks_last_json_line():
    sandbox = _FakeSandbox(stdout="litellm: noisy log\n{\"exit_status\": \"Submitted\", \"submission\": \"diff --git x\"}\n")
    agent = _agent()
    result = asyncio.run(agent.run(sandbox=sandbox, messages=[{"role": "user", "content": "task"}]))
    assert result.output["exit_status"] == "Submitted"
    assert result.output["submission"] == "diff --git x"


def test_unparseable_stdout_is_reported_instead_of_raising():
    sandbox = _FakeSandbox(stdout="mini-swe-agent banner\ngarbage\n")
    agent = _agent()
    result = asyncio.run(agent.run(sandbox=sandbox, messages=[{"role": "user", "content": "task"}]))
    assert result.output["exit_status"] == "error"
    assert "error" in str(result.output)
    assert result.finished is False  # not "Submitted" -> unfinished episode


def test_empty_stdout_is_reported_instead_of_raising():
    sandbox = _FakeSandbox(stdout="")
    agent = _agent()
    result = asyncio.run(agent.run(sandbox=sandbox, messages=[{"role": "user", "content": "task"}]))
    assert result.output["exit_status"] == "error"
    assert result.finished is False


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))

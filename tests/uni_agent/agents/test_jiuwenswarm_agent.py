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

import pytest

from uni_agent.framework.task_runner import _inject_gateway_tunnel, _reward_info_from_result
from uni_agent.tasks import TaskResult


def test_task_result_positional_field_order():
    result = TaskResult(0.5, 1.0, False, {"reason": "limit"})

    assert result.reward == 0.5
    assert result.accuracy == 1.0
    assert result.finished is False
    assert result.extra_info == {"reason": "limit"}


def test_reward_info_omits_unknown_agent_completion():
    result = TaskResult(reward=0.5, accuracy=1.0)

    assert _reward_info_from_result(result) == {
        "reward": 0.5,
        "acc": 1.0,
    }


@pytest.mark.parametrize("finished", [True, False])
def test_reward_info_forwards_agent_completion(finished):
    result = TaskResult(reward=0.0, finished=finished)

    assert _reward_info_from_result(result) == {
        "reward": 0.0,
        "finished": finished,
    }


def test_reward_info_rejects_non_boolean_agent_completion():
    result = TaskResult(reward=0.0, finished=0)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="finished must be a bool or None"):
        _reward_info_from_result(result)


# --------------------------- gateway tunnel injection ---------------------------


def test_gateway_tunnel_injects_upstream_and_syncs_agent_port():
    task = {
        "name": "swe_rebench",
        "sandbox": {"provider": "openyuanrong", "sandbox_kwargs": {"proxy_port": 38197}},
        "agent": {"name": "mini_swe_agent"},
    }

    merged = _inject_gateway_tunnel(task, "http://8.92.9.155:40169/sessions/abc/v1")

    assert merged["sandbox"]["sandbox_kwargs"]["upstream"] == "8.92.9.155:40169"
    assert merged["sandbox"]["sandbox_kwargs"]["proxy_port"] == 38197
    # the agent's rewrite target stays in sync with the sandbox tunnel port
    assert merged["agent"]["proxy_port"] == 38197
    # unrelated fields are preserved
    assert merged["sandbox"]["provider"] == "openyuanrong"
    assert merged["agent"]["name"] == "mini_swe_agent"


def test_gateway_tunnel_preserves_existing_sandbox_kwargs():
    task = {
        "sandbox": {"sandbox_kwargs": {"proxy_port": 50000, "mounts": [{"target": "/opt/mini-swe-agent"}]}},
        "agent": {},
    }

    merged = _inject_gateway_tunnel(task, "http://host:8000/v1")

    assert merged["sandbox"]["sandbox_kwargs"]["upstream"] == "host:8000"
    assert merged["sandbox"]["sandbox_kwargs"]["proxy_port"] == 50000
    assert merged["sandbox"]["sandbox_kwargs"]["mounts"] == [{"target": "/opt/mini-swe-agent"}]
    assert merged["agent"]["proxy_port"] == 50000


def test_gateway_tunnel_requires_host_and_port():
    with pytest.raises(ValueError, match="cannot derive gateway tunnel upstream"):
        _inject_gateway_tunnel(
            {"sandbox": {"sandbox_kwargs": {"proxy_port": 38197}}, "agent": {}},
            "not-a-url",
        )

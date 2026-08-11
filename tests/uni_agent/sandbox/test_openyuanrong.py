"""Tests for the openyuanrong sandbox provider's image mapping."""

from __future__ import annotations

import pytest

from uni_agent.sandbox.base import SandboxConfig
from uni_agent.sandbox.openyuanrong import OpenyuanrongSandbox, _to_openyuanrong_image


def test_canonical_swebench_ref_is_prefixed_with_registry():
    assert _to_openyuanrong_image("swebench/sweb.eval.x86_64.astropy__astropy-12907") == (
        "swr.cn-east-3.myhuaweicloud.com/openyuanrong/swebench/sweb.eval.x86_64.astropy__astropy-12907"
    )


def test_canonical_swerebench_ref_is_prefixed_with_registry():
    assert _to_openyuanrong_image("swerebench/sweb.eval.x86_64.django__django-11049") == (
        "swr.cn-east-3.myhuaweicloud.com/openyuanrong/swerebench/sweb.eval.x86_64.django__django-11049"
    )


def test_full_address_passes_through_unchanged():
    tool_image = "swr.cn-east-3.myhuaweicloud.com/openyuanrong/mini-swe-agent-tool:latest"
    assert _to_openyuanrong_image(tool_image) == tool_image


def test_from_config_applies_image_mapping():
    config = SandboxConfig(
        provider="openyuanrong",
        image="swebench/sweb.eval.x86_64.astropy__astropy-12907",
        sandbox_kwargs={"proxy_port": 38197},
    )
    sandbox = OpenyuanrongSandbox.from_config(config)
    assert sandbox.image.startswith("swr.cn-east-3.myhuaweicloud.com/openyuanrong/")
    assert sandbox.proxy_port == 38197


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))

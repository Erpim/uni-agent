"""Tests for the jiuwenswarm context-compaction trajectory filter.

The distinction under test: the two markers appear only in the compression
request, so a trajectory that was already compacted (its history carries
``<memory_block>``) must still be kept.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from examples.jiuwenswarm_swe_task.jiuwenswarm_compaction_filter import (
    _COMPACTION_MARKERS,
    classify,
    select_without_compaction,
)

# Copied from openjiuwen==0.1.18 (the workswarm==0.2.6 pin), prompts/prompts.py.
_COMPACTION_PROMPT = (
    "## NON-NEGOTIABLE OUTPUT RULES\n\n"
    "Your response must contain exactly two XML blocks:\n\n"
    "<coverage_check>\n...\n</coverage_check>\n\n"
    "<state_snapshot>\n...\n</state_snapshot>\n"
)

# From base.py::_wrap_memory_block; this is what a compacted task trajectory holds.
_INJECTED_SUMMARY = (
    "<memory_block>\n<meaning>\nThis is compressed context from earlier messages.\n</meaning>\n"
    "<summary>\nFixed the import ordering.\n</summary>\n</memory_block>"
)


def _traj(messages: list[dict], chain_id: int = 1):
    return SimpleNamespace(extra_fields={"message_history": messages}, chain_id=chain_id, response_mask=[1, 1])


def _compaction_traj(chain_id: int = 2):
    return _traj([{"role": "user", "content": _COMPACTION_PROMPT}], chain_id=chain_id)


def _work_traj(chain_id: int = 1, compacted: bool = False):
    messages = [
        {"role": "user", "content": "separability_matrix is wrong for nested models."},
        {"role": "assistant", "content": "Looking at separable.py."},
    ]
    if compacted:
        messages.insert(0, {"role": "user", "content": _INJECTED_SUMMARY})
    return _traj(messages, chain_id=chain_id)


@pytest.mark.cpu
@pytest.mark.level0
def test_compaction_turn_is_dropped():
    assert classify(_compaction_traj()) == "compaction"


@pytest.mark.cpu
@pytest.mark.level0
def test_compacted_work_chain_is_kept():
    """memory_block marks a trajectory as compacted, which is a reason to keep it."""
    assert classify(_work_traj(compacted=True)) == "unknown"


@pytest.mark.cpu
@pytest.mark.level0
def test_filter_keeps_only_work():
    trajectories = [_work_traj(chain_id=1, compacted=True), _compaction_traj(chain_id=2), _work_traj(chain_id=3)]
    assert [t.chain_id for t in select_without_compaction("s", trajectories)] == [1, 3]


@pytest.mark.cpu
@pytest.mark.level0
def test_missing_history_is_kept():
    bare = SimpleNamespace(extra_fields={}, chain_id=1, response_mask=[1])
    assert classify(bare) == "unknown"


@pytest.mark.cpu
@pytest.mark.level0
def test_marker_list():
    assert _COMPACTION_MARKERS == ["<coverage_check>", "<state_snapshot>"]

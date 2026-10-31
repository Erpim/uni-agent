"""Drop jiuwenswarm context-compaction turns (RFC-0001 ``custom_select_function``).

openjiuwen's compressors (dialogue / current_round / round_level, triggering at
0.8 of the context window) make an extra model call on compaction, asking the
model for an execution checkpoint:

    <coverage_check> ... </coverage_check>
    <state_snapshot> ... </state_snapshot>

That call runs through the same policy gateway as the real work, so it forms its
own trajectory. It carries no task signal, so it is dropped.

These two markers only appear in the compression *request*, which is why
matching on them cannot hit a task trajectory: openjiuwen's
``_extract_state_snapshot_or_raw`` strips both tags and re-wraps the body as
``<memory_block>`` inside the trajectory that was compacted. ``<memory_block>``
is therefore evidence that a trajectory *was* compacted and must be kept, never
a reason to drop it.
"""

from __future__ import annotations

_COMPACTION_MARKERS = [
    "<coverage_check>",
    "<state_snapshot>",
]


def _flatten_message_text(trajectory: object) -> str:
    extra_fields = getattr(trajectory, "extra_fields", None) or {}
    history = extra_fields.get("message_history") or []
    parts = []
    for message in history:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def classify(trajectory: object) -> str:
    text = _flatten_message_text(trajectory)
    if any(marker in text for marker in _COMPACTION_MARKERS):
        return "compaction"
    return "unknown"


def select_without_compaction(session_id: str, trajectories: list) -> list:
    """Keep every trajectory that is not a context-compaction turn."""
    return [trajectory for trajectory in trajectories if classify(trajectory) != "compaction"]

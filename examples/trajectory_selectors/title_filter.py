"""Sample trajectory select functions for RFC-0001 custom selection.

These are *examples*, not a library: copy the shape you need into your own
recipe and tune the markers for your agent. They are loaded through the
two-part ``custom_select_function`` config:

    trajectory_selection:
      custom_select_function:
        path: /abs/path/to/title_filter.py     # or pkg://some.pkg.module
        name: select_without_title_segments

Prefer a **file path** over ``pkg://examples.*``: ``examples/`` has no
``__init__.py``, so it is a namespace package whose resolution depends on the
order of ``sys.path`` (a second checkout earlier on the path would win silently).
A plain path or ``pkg://your.own.package.module`` is unambiguous.

Contract (what the framework guarantees):

* Called once per session, after finalization and before reward scoring.
* ``trajectories`` is the session's full list, in the gateway's close order.
* Each trajectory carries ``chain_id`` (creation order: strictly increasing,
  immutable) and, because custom selection is configured, a
  ``extra_fields["message_history"]`` holding the raw chat text.
* Whatever you return is kept as-is, in your order. An empty return means
  "drop this session" and it takes the framework's existing ``status="empty"``
  path (which counts as a failed session).
* A raised exception falls back to keeping everything.
* ``message_history`` is released by the framework right after your function
  returns, for kept *and* dropped trajectories. Do not stash trajectories (or
  their text) anywhere that outlives the call, and treat the message dicts as
  read-only: they are shared with the gateway session state.
"""

from __future__ import annotations

#: A caller generating a session title before/after the real work.
TITLE_MARKERS = (
    "Create a concise title for an AI coding-assistant session",
    "Generate the session title from this JSON array of human messages",
    "Return only the title on one line",
)

#: A context-compaction segment replaying a summarized history.
COMPACTION_MARKERS = (
    "<state_snapshot>",
    "<coverage_check>",
    "compacted into",
)

#: A subagent shell segment carrying someone else's work.
SUBAGENT_MARKERS = (
    "You are a subagent",
    "spawned by main agent",
)


def _text_of(trajectory) -> str:
    """Join every message content of a trajectory into one searchable string."""
    history = trajectory.extra_fields.get("message_history") or []
    parts = []
    for message in history:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
    return " ".join(parts)


def _system_text_of(trajectory) -> str:
    """Only the system messages: title prompts live there."""
    history = trajectory.extra_fields.get("message_history") or []
    return " ".join(message.get("content", "") for message in history if message.get("role") == "system")


def is_title_segment(trajectory) -> bool:
    """A segment whose system prompt asks the model to write a session title."""
    system_text = _system_text_of(trajectory)
    return any(marker in system_text for marker in TITLE_MARKERS)


def classify_segment(trajectory) -> str:
    """Coarse label for a segment (an internal device; the framework never sees it).

    Labelling here is only a convenient way to express the rule below. The
    framework does not know or care that labels exist.
    """
    if is_title_segment(trajectory):
        return "title"
    text = _text_of(trajectory)
    if any(marker in text for marker in COMPACTION_MARKERS):
        return "compaction"
    if any(marker in text for marker in SUBAGENT_MARKERS):
        return "subagent"
    if sum(trajectory.response_mask) > 0:
        return "main"
    return "unknown"


def select_without_title_segments(session_id: str, trajectories: list) -> list:
    """Keep everything except title-generation segments.

    The minimal, most readable form: one content test, no ordering assumptions.
    """
    return [trajectory for trajectory in trajectories if not is_title_segment(trajectory)]


def select_work_segments(session_id: str, trajectories: list) -> list:
    """Keep only segments that look like real work, in creation order.

    Shows the two things the framework deliberately does not do for you:
    filtering by an internally computed label, and sorting by ``chain_id``
    (which is the creation order; the input order is the gateway's close order).
    """
    kept = [
        trajectory
        for trajectory in trajectories
        if classify_segment(trajectory) not in {"title", "compaction", "subagent"}
    ]
    return sorted(kept, key=lambda trajectory: (trajectory.chain_id is None, trajectory.chain_id or 0))

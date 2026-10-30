"""Select-function fixtures shared by the framework CPU tests.

Kept in a separate importable module on purpose: the framework loads a custom
select function through ``{path, name}`` (``pkg://...`` or a file path). Using a
real module lets a test import the same object the framework loaded and inspect
the call log, which a lambda defined inside the test could not offer.

The functions here are deliberately tiny; they exist to exercise the framework
side of the contract (dispatch, fallback, release), not to model a real
selection policy.
"""

from __future__ import annotations

#: Every call recorded as ``(session_id, [id(trajectory), ...])``.
CALLS: list[tuple[str, list[int]]] = []


def record_calls(session_id: str, trajectories: list) -> list:
    """Return every trajectory unchanged while recording the call."""
    CALLS.append((session_id, [id(trajectory) for trajectory in trajectories]))
    return list(trajectories)


def drop_first(session_id: str, trajectories: list) -> list:
    """Keep everything but the first trajectory (creation order)."""
    CALLS.append((session_id, [id(trajectory) for trajectory in trajectories]))
    return list(trajectories[1:])


def reverse_order(session_id: str, trajectories: list) -> list:
    """Return the trajectories in the caller's requested order."""
    CALLS.append((session_id, [id(trajectory) for trajectory in trajectories]))
    return list(reversed(trajectories))


def drop_everything(session_id: str, trajectories: list) -> list:
    """Deliberately keep nothing so the session follows status="empty"."""
    CALLS.append((session_id, [id(trajectory) for trajectory in trajectories]))
    return []


def raise_boom(session_id: str, trajectories: list) -> list:
    """Fail loudly so the framework has to fall back to 'all'."""
    CALLS.append((session_id, [id(trajectory) for trajectory in trajectories]))
    raise RuntimeError("select boom")


async def async_record_calls(session_id: str, trajectories: list) -> list:
    """Async variant: awaitable results must be awaited, not used raw."""
    import asyncio

    await asyncio.sleep(0)
    CALLS.append((session_id, [id(trajectory) for trajectory in trajectories]))
    return list(trajectories)


#: Deliberately not a function, to prove the loader rejects non-callables.
NOT_CALLABLE = "not-a-function"

# Custom trajectory selection (RFC-0001) — configuration snippets.
#
# Not a runnable config: copy the `trajectory_selection` block into
# `agent_runners.<name>` of your recipe's `run_train.sh` overrides.
#
# Two type-disjoint forms are accepted. There is no `mode:` field — the form
# itself is the mode. The string "custom" is accepted nowhere in user config
# (it only exists as an internal marker in the framework).

agent_runners:
  task:

    # Form 1 (unchanged, default): keep every trajectory.
    trajectory_selection: all

    # Form 2 (unchanged): keep the single trajectory with the most model tokens,
    # tie-broken by response length, then turn count, then input order.
    # trajectory_selection: longest

    # Form 3 (new): call a user-owned function and keep whatever it returns.
    # The framework loads {path, name} at start-up and fails fast on a bad
    # reference (unimportable module, missing attribute, non-callable).
    # trajectory_selection:
    #   custom_select_function:
    #     # Preferred: an absolute file path, also valid with a `file://` prefix.
    #     path: /abs/path/to/examples/trajectory_selectors/title_filter.py
    #     name: select_without_title_segments
    #     # Or, for a package you install: path: pkg://my_pkg.trajectory_select
    #
    # Avoid `pkg://examples.<something>`: examples/ has no __init__.py, so it is
    # a namespace package and the import resolves against whichever checkout
    # happens to come first on sys.path.

# What the select function receives, per session:
#   select_fn(session_id: str, trajectories: list[Trajectory]) -> list[Trajectory]
# - trajectories: the session's full list, in gateway close order
# - each trajectory: chain_id (creation order), response_ids, response_mask,
#   num_turns, finished, and extra_fields["message_history"] (raw chat text)
#
# What the framework does with the result:
# - keeps it verbatim, in your order; no re-sorting, no injection
# - an empty return means "drop this session" (status="empty", counted as a
#   failed session; no TQ write, no reward)
# - a raised exception logs and falls back to keeping all trajectories
# - message_history is released immediately afterwards, for kept and dropped
#   trajectories alike, before reward scoring, artifact logging and TQ writes.
#   A configured `trajectory_postprocessor` therefore cannot read it.
#
# Async select functions are supported: a coroutine result is awaited.

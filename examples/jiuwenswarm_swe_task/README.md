# jiuwenswarm Blackbox Sidecar Integration

Integrates the [jiuwenswarm](https://gitcode.com/openJiuwen/jiuwenswarm) agent
(PyPI: `workswarm==0.2.6`) into uni-agent as a sidecar tool image for SWE-bench
code repair tasks.

## Architecture

- **Tool image**: `Dockerfile.jiuwenswarm-tool` builds a `FROM scratch` image
  with python-build-standalone + `workswarm` + `run_agent.sh` entrypoint.
- **Sandbox**: openyuanrong remote sandbox with the tool image mounted at
  `/opt/jiuwenswarm`.
- **Host agent**: `uni_agent/agents/jiuwenswarm/agent.py` (`JiuwenswarmAgent`)
  base64-pipes the task prompt into the bash entrypoint, passes the gateway
  config via env vars, and parses the result JSON.
- **Task config**: `task_config_jiuwenswarm.yaml` declares sandbox mounts and
  agent params for two tasks sharing one prompt template: `swe_rebench` (RL
  training) and `swe_bench` (evaluation).
- **Training**: `run_train.sh` launches Megatron async PPO training.
- **Inference**: `run_infer_jiuwenswarm.sh` runs the same recipe through verl's
  inference path (`examples/inference/parallel_infer_verl.py`).

## Build

```bash
# Build the tool image
bash examples/jiuwenswarm_swe_task/build_tool.sh

# Build with a custom pip index (e.g., mirror)
bash examples/jiuwenswarm_swe_task/build_tool.sh --pip-index https://pypi.tuna.tsinghua.edu.cn/simple/

# Build, tag and push to a registry
bash examples/jiuwenswarm_swe_task/build_tool.sh --registry swr.cn-east-3.myhuaweicloud.com/openyuanrong
```

The recipe consumes a prebuilt tool image (see `sandbox_kwargs.mounts[].image_url`
in `task_config_jiuwenswarm.yaml`); rebuild only when you change `run_agent.sh`
or the pinned versions in `Dockerfile.jiuwenswarm-tool`.

## Run (inference)

```bash
export OPENYUANRONG_SERVER_ADDRESS="<server address>"
export OPENYUANRONG_TOKEN="<token>"

LIMIT=1 N=1 CONCURRENCY=1 TENSOR_PARALLEL_SIZE=2 \
  bash examples/jiuwenswarm_swe_task/run_infer_jiuwenswarm.sh
```

Override `DATA_PATH`, `MODEL_PATH`, `TASK_CONFIG`, `LOG_DIR` and `RESULT_PATH`
as needed. The Qwen3.5 checkpoint declares a vision tower, so the driver always
passes `--language-model-only`.

## Training

```bash
bash examples/jiuwenswarm_swe_task/run_train.sh
```

## Trajectory selection: dropping context-compaction turns

jiuwenswarm enables openjiuwen's context engine with three forked compressors
(`dialogue`, `current_round`, `round_level`, all triggering at 0.8 of the
context window). Each firing is an **extra model call** asking for an execution
checkpoint in `<coverage_check>` + `<state_snapshot>` blocks. That call runs
through the same policy gateway, so it **materializes as its own trajectory**:
a summarization turn the policy did not need to learn.

`run_train.sh` always wires `jiuwenswarm_compaction_filter.py` in as the
runner's `custom_select_function` (RFC-0001), so those turns are dropped:

```bash
SELECT_FUNCTION_PY="${REPO_ROOT}/examples/jiuwenswarm_swe_task/jiuwenswarm_compaction_filter.py"
SELECT_FUNCTION_NAME="select_without_compaction"
```

The filter keys off the two XML tags of the compression **request**. It must not
key off `<memory_block>`: openjiuwen's `_extract_state_snapshot_or_raw` strips the
tags and `_wrap_memory_block` then injects `<memory_block><summary>…</summary></memory_block>`
into the **working** trajectory that did the real work. A filter matching
`<memory_block>` would delete the task trajectory right after its first
compaction.

When a select function is configured the gateway also copies the chain's chat
text into `extra_fields["message_history"]`; the framework releases it right after
the selection step, before reward scoring and the TransferQueue write.

## Files

| File | Purpose |
|---|---|
| `Dockerfile.jiuwenswarm-tool` | Sidecar tool image build |
| `build_tool.sh` | Build/push helper |
| `run_agent.sh` | In-sandbox bash entrypoint |
| `task_config_jiuwenswarm.yaml` | Task + sandbox defaults |
| `run_train.sh` | Megatron RL training launch |
| `run_infer_jiuwenswarm.sh` | Inference launch |
| `jiuwenswarm_compaction_filter.py` | RFC-0001 selector dropping compression turns |
| `README.md` | This file |

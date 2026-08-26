# jiuwenswarm Blackbox Sidecar Integration

Integrates the [jiuwenswarm](https://gitcode.com/openJiuwen/jiuwenswarm) agent
(PyPI: `workswarm==0.2.5`) into uni-agent as a sidecar tool image for SWE-bench
code repair tasks.

## Architecture

- **Tool image**: `Dockerfile.jiuwenswarm-tool` builds a `FROM scratch` image
  with python-build-standalone + `workswarm` + `run_agent.sh` entrypoint.
- **Sandbox**: openyuanrong remote sandbox with the tool image mounted at
  `/opt/jiuwenswarm`.
- **Host agent**: `uni_agent/agents/jiuwenswarm/JiuwenswarmAgent` base64-pipes
  the task prompt into the bash entrypoint, passes the gateway config via env
  vars, and parses the result JSON.
- **Task config**: `task_config_jiuwenswarm.yaml` declares sandbox mounts and
  agent params for the `swe_bench` task.
- **Training**: `run_train.sh` launches Megatron async PPO training.

## Build

```bash
# Build the tool image
bash examples/jiuwenswarm/build_tool.sh

# Build with a custom pip index (e.g., mirror)
bash examples/jiuwenswarm/build_tool.sh --pip-index https://pypi.tuna.tsinghua.edu.cn/simple/

# Build, tag and push to a registry
bash examples/jiuwenswarm/build_tool.sh --registry swr.cn-east-3.myhuaweicloud.com/openyuanrong
```

## Run (standalone evaluator)

```bash
python -m uni_agent.framework.task_runner \
  --task_config examples/jiuwenswarm/task_config_jiuwenswarm.yaml \
  --sample '{"instance_id": "django__django-12345", "image": "swebench/sweb.eval.x86_64.django__django-12345", ...}'
```

## Training

```bash
bash examples/jiuwenswarm/run_train.sh
```

## Files

| File | Purpose |
|---|---|
| `Dockerfile.jiuwenswarm-tool` | Sidecar tool image build |
| `build_tool.sh` | Build/push helper |
| `run_agent.sh` | In-sandbox bash entrypoint |
| `task_config_jiuwenswarm.yaml` | Task + sandbox defaults |
| `run_train.sh` | Megatron RL training launch |
| `DESIGN.md` | Design document |
| `README.md` | This file |

## Key differences from mini_swe_agent

- Uses **bash entrypoint** (`run_agent.sh`) instead of Python (`run_agent.py`)
- Task prompt piped via **stdin (base64)**; gateway config passed via **env vars**
  (`JWS_API_BASE` / `JWS_MODEL_NAME` / `JWS_API_KEY` / `JWS_PROJECT_DIR`)
- `finished = ok == true` (from jiuwenswarm's `--json` output), not
  `exit_status == "Submitted"`
- Runtime config of max_iterations / subagents is **not supported** in v1
  (uses jiuwenswarm defaults: `react.max_iterations: 100`)
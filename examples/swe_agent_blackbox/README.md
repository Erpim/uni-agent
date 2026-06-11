# Mini-SWE-Agent In-Sandbox Execution — 使用指南

## 概述

mini-swe-agent 以 sidecar 工具镜像的形式挂载到 OpenYuanRong 远程沙箱内部运行。Agent 在沙箱内通过
`LocalEnvironment`（本地 bash）执行命令，LLM 调用走 stdin 传入的 gateway URL。
外部 runner 创建沙箱、触发 agent 执行、评估 reward。

工具镜像使用 [python-build-standalone](https://github.com/astral-sh/python-build-standalone) 构建独立 Python 环境，不依赖沙箱容器内的 Python 版本，通过 `FROM scratch` 保持镜像最小化。

## 架构

```
[Rollouter Host: mini_swe_agent_runner]
  │
  ├── _create_sandbox(image, sidecar_image)
  │     └── OpenYuanRong: Sandbox(mounts=[Mount(target="/opt/mini-swe-agent", ...)])
  │
  ├── sandbox.run("echo <b64_config> | base64 -d | /opt/.../python run_agent.py")
  │     └── [Inside Sandbox]
  │           /opt/mini-swe-agent/bin/python  ← 独立 Python，不影响沙箱原有版本
  │           stdin ← task config JSON (task, gateway_url, agent)
  │           LocalEnvironment + LitellmModel(gateway_url) → DefaultAgent
  │           stdout → result JSON (exit_status, submission, model_stats)
  │
  ├── _parse_agent_result(stdout)
  ├── SandboxEnvForReward(sandbox) → evaluate_in_env()
  └── session_runtime.complete_session(reward_info)
```

## 前置条件

1. **OpenYuanRong 环境** — 需配置 `OPENYUANRONG_SERVER_ADDRESS`、`OPENYUANRONG_TOKEN`
2. **mini-swe-agent tool image** — 需预先构建并推送到远程仓库（见下文）

## 1. 构建 Tool Image

Tool image 包含独立 Python 环境和 mini-swe-agent，作为 sidecar 挂载到 YR 沙箱中运行。
构建一次推送到远程仓库后即可复用，无需每次运行前重新构建。

```bash
bash examples/swe_agent_blackbox/build_tool.sh --registry <your-registry>
```

可选参数：
- `--pip-index` — 指定 pip 镜像源加速下载

构建产物为 `FROM scratch` 最小镜像，仅包含 Python + mini-swe-agent + litellm。
Runner 通过 `MINI_SWE_AGENT_IMAGE` 环境变量引用。

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TOOL_IMAGE` | `mini-swe-agent-tool` | 镜像名 |
| `TOOL_TAG` | `latest` | 镜像 tag |

## 2. 数据集

使用 `data_preprocess` 生成训练/推理数据，格式为 parquet。

```bash
DEPLOYMENT=openyuanrong python examples/data_preprocess/swe_bench_verified.py --local-save-dir ~/data/swe_agent
```

输出文件：`~/data/swe_agent/swe_bench_verified_openyuanrong.parquet`，主要字段：

| 字段 | 说明 |
|------|------|
| `prompt` | 任务描述（chat message 列表，含 system + user） |
| `agent_name` | Agent 类型标识（如 `swe_agent`） |
| `extra_info.tools_kwargs.env.deployment` | 沙箱配置（含 `image` 镜像地址） |
| `extra_info.tools_kwargs.reward` | Reward 评估配置（FAIL_TO_PASS、PASS_TO_PASS 等） |

## 3. 推理

### 环境变量

```bash
export OPENYUANRONG_SERVER_ADDRESS="6.2.179.37:8888"
export OPENYUANRONG_TOKEN="<your-token>"
```

### 使用 run_infer.sh

```bash
RUNNER=mini_swe \
OPENYUANRONG_SERVER_ADDRESS="6.2.179.37:8888" \
OPENYUANRONG_TOKEN="<token>" \
bash examples/swe_agent_blackbox/scripts/run_infer.sh
```

## 4. 训练（全异步）

```bash
OPENYUANRONG_SERVER_ADDRESS="6.2.179.37:8888" \
OPENYUANRONG_TOKEN="<token>" \
MODEL_PATH=~/models/Qwen3.5-9B \
bash examples/swe_agent_blackbox/scripts/run_train_megatron_async.sh
```

## 5. 配置参数

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SWE_AGENT_MAX_TURNS` | `100` | Agent 最大步数 |
| `MINI_SWE_AGENT_IMAGE` | `swr.cn-east-3.myhuaweicloud.com/openyuanrong/mini-swe-agent-tool:latest` | sidecar 工具镜像 |
| `DEBUG_MODE` | (unset) | 设为 1 开启 DEBUG 日志 |

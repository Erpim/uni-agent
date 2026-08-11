# mini_swe_agent recipe 适配新框架设计文档

> 目标：把 `uni-agent_zzq/examples/blackbox_recipes/mini_swe_agent/`（重构前 runner 流程）适配到 `uni-agent`（PR #83 之后的 Task/Agent/Sandbox 框架）。
>
> **总原则：老流程 → 新流程改动最小。**
> - 测试不是契约，`tests/uni_agent/agents/test_mini_swe_agent_agent.py` **按新行为重写**。
> - tool 镜像、`Dockerfile`、`build_tool.sh`、`run_agent.py`、stdin/stdout 协议、隧道端口全部**原样保留**。
> - agent 实现 = 老 `mini_swe_agent_runner.py` 中「组 config → 管道进沙箱 → 解析 stdout」三段原样搬进 `uni_agent/agents/mini_swe_agent/agent.py`；沙箱创建/回收与 reward 由新框架的 Task 承接。

---

## 1. 背景

### 1.1 老流程（`uni-agent_zzq`，重构前）

入口 `run_train.sh` → `verl.trainer.main_ppo`（V1, `separate_async`, 4+4 GPU）。
经 `agent_loop_manager_class=AgentFrameworkRolloutAdapter` + `agent_runners.swe_agent.runner_fqn` 指向 recipe 内 runner。

`mini_swe_agent_runner.py` 5 步：
1. `tools_kwargs.env.image` 取沙箱镜像；`extract_upstream(gateway_url)` 建反向隧道。
2. 建 AKernel `SandboxClient`，`Mount` tool 镜像到 `/opt/mini-swe-agent`，`upstream` + `proxy_port=38197`。
3. `_build_task_config(task, gateway_url)`：把 gateway_url 改写成 `http://127.0.0.1:38197/v1`，连同 `task`、`agent.step_limit` 组 JSON → base64 → **stdin** 传给 `/opt/mini-swe-agent/bin/python /opt/mini-swe-agent/bin/run_agent.py`。
4. 从 **stdout** 取「最后一个 `{` 开头行」解析 `{exit_status, submission, model_stats}`。
5. 同沙箱 `evaluate_in_env` 算分，POST `reward_info` 到 `session.reward_info_url`。

`run_agent.py`（打进 tool 镜像，原样保留）：stdin 读 `{task, gateway_url, agent:{step_limit}}` → swebench 默认配置的 `LocalEnvironment` + `LitellmModel(api_base=gateway_url)` + `DefaultAgent` → stdout 写 `{exit_status, submission, model_stats}`。

tool 镜像：python-build-standalone + `mini-swe-agent==2.2.8` + `litellm==1.81.7` + `run_agent.py`，`FROM scratch` 打到镜像根（Mount 后落在 `/opt/mini-swe-agent`）。

### 1.2 新框架（`uni-agent` @main，PR #83 后）

- 统一 `Task`/`Agent`/`Sandbox` 抽象 + 注册表；reward 移入 Task。
- 训练统一走 **`run_task` 桥接**：`agent_runners.task.runner_fqn=uni_agent.framework.task_runner.run_task`。
- 数据每行携带 `extra_info.tools_kwargs.task`；`TaskConfigResolver` 做「文件默认值 → sample 值 → 运行时模型」三级深合并；运行时把 `session.base_url` 等注入 `agent.model`。
- `run_task` 内 `get_task(task).run()` 返回 `TaskResult`；`report_reward=True` 时 POST 回 session。

### 1.3 决策要点（用户确认）

| 问题 | 决策 | 设计影响 |
|---|---|---|
| 网关可达性 | **需要反向隧道** | `run_task` 注入沙箱 `upstream`（端口写死 task_config `sandbox_kwargs.proxy_port`，option A）；agent 内改写成 `http://127.0.0.1:<port>/v1` |
| 安装方式 | **保留 tool 镜像预构建，Dockerfile 零改动** | 无 install 步骤；直接用 `/opt/mini-swe-agent/bin/python .../run_agent.py` |
| 数据 | **重跑新 preprocess** | `swe_rebench.preprocess` / `swe_bench.preprocess` |
| 镜像名 | **需要 registry 前缀** | openyuanrong provider 侧 canonical→registry 映射 |
| 测试 | **不是契约，可改** | 按新 agent 行为重写 `test_mini_swe_agent_agent.py` |

---

## 2. 目标架构

```
verl.main_ppo
  └─ AgentFrameworkRolloutAdapter ── GatewayManager (session.base_url = 策略网关)
        └─ OpenAICompatibleAgentFramework ── run_task (runner_fqn)
              └─ TaskConfigResolver：task_config YAML + tools_kwargs.task(sample) + 运行时模型
                    │  └─ [小改] 隧道注入：仅填 sandbox.sandbox_kwargs.upstream（端口读 YAML proxy_port）+ 同步 agent.proxy_port
                    └─ get_task("swe_rebench").run()
                          └─ SWEREBenchTask.run()
                                ├─ build_sandbox() ── OpenyuanrongSandbox（from_config 做镜像映射）
                                │      mounts: [tool 镜像 → /opt/mini-swe-agent]
                                │      upstream/proxy_port：run_task 隧道注入
                                ├─ build_agent() ── MiniSweAgentAgent
                                │      rewrite_gateway_url(base_url) → b64 stdin → exec_shell
                                │      /opt/mini-swe-agent/bin/python .../run_agent.py → 解析 stdout
                                └─ compute_reward() ── swe_rebench.reward（沙箱内打分）
```

**改动总览**（比上一版更小）：

| 文件 | 动作 | 规模 |
|---|---|---|
| `uni_agent/agents/mini_swe_agent/agent.py` | 实现（搬老 runner 核心 + 配置类） | 新文件 ~90 行 |
| `tests/uni_agent/agents/test_mini_swe_agent_agent.py` | **重写**为匹配新行为 | 覆盖 |
| `uni_agent/framework/task_runner.py` | 加隧道 `upstream` 注入（端口读 task_config `proxy_port`） | +~15 行 |
| `uni_agent/sandbox/openyuanrong.py` | 加 `_to_openyuanrong_image` 镜像映射 | +~10 行 |
| `examples/blackbox_recipes/mini_swe_agent/` | 新增 task_config；改 run_train.sh；删旧 runner/reward/dataset/parallel_infer/config | 见 §9 |
| `Dockerfile.mini-swe-agent-tool` / `build_tool.sh` / `run_agent.py` | **零改动** | — |
| 训练/验证数据 | 重跑 preprocess | — |

不改动：`uni_agent/tasks/swe_rebench`、`swe_bench`（Task/Reward 已就绪）；`OpenyuanrongSandbox` 本体。

---

## 3. `uni_agent/agents/mini_swe_agent/agent.py` 实现

把老 runner 的核心逻辑原样搬入 `Agent.run()`，新框架只提供 `sandbox`（已 start）与 `messages`。

```python
"""mini-swe-agent: black-box agent launched *inside* the sandbox.

Re-homes the old recipe runner's core (config → stdin → parse stdout) into the
new Agent contract. The tool image (prebuilt venv at /opt/mini-swe-agent) and
the in-image run_agent.py are reused unchanged.
"""

from __future__ import annotations
import base64, json, logging, shlex
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from pydantic import Field
from ..base import Agent, AgentConfig, AgentResult
from ..registry import register_agent

DEFAULT_PROXY_PORT = 38197
TOOL_PYTHON = "/opt/mini-swe-agent/bin/python"
RUN_AGENT_SCRIPT = "/opt/mini-swe-agent/bin/run_agent.py"


def extract_upstream(gateway_url: str) -> str:
    parsed = urlparse(gateway_url)
    return f"{parsed.hostname}:{parsed.port}"


def rewrite_gateway_url(gateway_url: str, proxy_port: int = DEFAULT_PROXY_PORT, *, strip_v1: bool = False) -> str:
    """Rewrite gateway URL to the sandbox-internal tunnel (host:port → 127.0.0.1:<proxy_port>)."""
    parsed = urlparse(gateway_url)
    path = parsed.path.removesuffix("/v1") if strip_v1 else parsed.path
    return f"http://127.0.0.1:{proxy_port}{path}"


def build_agent_command(
    *, config_b64: str, conda_env: str = "testbed",
    tool_python: str = TOOL_PYTHON, run_agent_script: str = RUN_AGENT_SCRIPT,
) -> str:
    """Build the command that runs run_agent.py inside the sandbox (unchanged protocol)."""
    conda_prefix = f"/opt/miniconda3/envs/{conda_env}"
    run_agent_env = (
        f"CONDA_DEFAULT_ENV={shlex.quote(conda_env)} "
        f"CONDA_PREFIX={shlex.quote(conda_prefix)} "
        f"PATH={shlex.quote(conda_prefix + '/bin')}:/opt/miniconda3/bin:$PATH "
        "PIP_DISABLE_PIP_VERSION_CHECK=1 "
        "PIP_PROGRESS_BAR=off"
    )
    return (
        "unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy; "
        f"printf %s {shlex.quote(config_b64)} | base64 -d | "
        f"env {run_agent_env} {tool_python} {run_agent_script}"
    )


def parse_agent_result(stdout: str) -> dict:
    """Parse result JSON from run_agent.py stdout (last line starting with '{'; litellm may pollute)."""
    stdout = stdout.strip()
    if not stdout:
        return {"exit_status": "error", "submission": ""}
    for line in reversed([ln.strip() for ln in stdout.split("\n") if ln.strip()]):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"exit_status": "error", "submission": ""}


class MiniSweAgentConfig(AgentConfig):
    """Black-box launch params (endpoint lives on :attr:`AgentConfig.model`)."""

    name: str = "mini_swe_agent"
    step_limit: int = Field(default=100, description="mini-swe-agent max agent steps")
    run_timeout: float = Field(default=7200.0, description="agent 主进程超时")
    conda_env: str = Field(default="testbed", description="任务仓库 conda 环境名")
    proxy_port: int = Field(default=DEFAULT_PROXY_PORT, description="沙箱内隧道端口（与 sandbox 的 proxy_port 一致）")
    tool_python: str = Field(default=TOOL_PYTHON, description="tool 镜像内 python 路径")
    run_agent_script: str = Field(default=RUN_AGENT_SCRIPT, description="tool 镜像内入口脚本")


@register_agent("mini_swe_agent")
class MiniSweAgentAgent(Agent):
    config_model = MiniSweAgentConfig

    async def run(self, *, sandbox, messages) -> AgentResult:
        cfg: MiniSweAgentConfig = self.config
        if cfg.model.base_url is None:
            raise ValueError("mini_swe_agent: config.model.base_url is not set (the policy endpoint)")
        task = self._extract_task(messages)

        # 1) task config：gateway_url 改写为沙箱内隧道地址（老 _build_task_config 原样）
        task_config = {
            "task": task,
            "gateway_url": rewrite_gateway_url(cfg.model.base_url, cfg.proxy_port),
            "agent": {"step_limit": cfg.step_limit},
        }
        # 2) base64 管道进沙箱，跑预构建 tool 镜像里的 run_agent.py
        config_b64 = base64.b64encode(json.dumps(task_config).encode()).decode()
        agent_cmd = build_agent_command(
            config_b64=config_b64, conda_env=cfg.conda_env,
            tool_python=cfg.tool_python, run_agent_script=cfg.run_agent_script,
        )
        result = await sandbox.exec_shell(agent_cmd, timeout=cfg.run_timeout)
        # 3) 从 stdout 解析结果
        agent_info = parse_agent_result(result.stdout or "")

        # finished：显式完成判断。mini-swe-agent 只有提交成功才报 "Submitted"，
        # 其余（error/超时）视为未完成；配合框架 mask_unfinished_episode=True
        # 可将未完成 episode 从 loss 中排除。
        return AgentResult(
            output=agent_info,
            transcript=list(messages),
            info={"step_limit": cfg.step_limit, "exit_status": agent_info.get("exit_status")},
            finished=agent_info.get("exit_status") == "Submitted",
        )

    @staticmethod
    def _extract_task(messages) -> str:
        if len(messages) > 2:
            raise ValueError(f"mini_swe_agent accepts at most 2 messages (system?, user), got {len(messages)}")
        problem = next((m["content"] for m in messages if m.get("role") == "user"), None)
        if not problem:
            raise ValueError("mini_swe_agent requires a 'user' message (the problem statement)")
        return problem
```

要点：
- `build_agent_command` / `parse_agent_result` / `rewrite_gateway_url` / `extract_upstream` 均从老代码**原样搬移**，协议零变化。
- 隧道 URL 改写发生在 agent 内（老 `_build_task_config` 就在 runner 里做），`run_task` 不改写 base_url。
- 无 install、无 driver 写入、无 result.json —— 全部复用镜像内 `/opt/mini-swe-agent/bin/run_agent.py`。
- `finished = (exit_status == "Submitted")`：显式完成判断，供框架 `mask_unfinished_episode=True` 时排除未完成 episode；老流程无此机制，属可选增强（默认框架开关关着，行为仍与老流程一致）。

### 3.1 测试重写（`tests/uni_agent/agents/test_mini_swe_agent_agent.py`）

测试不再是「契约」，而是覆盖新行为的单测。`_FakeSandbox.exec_shell` 返回 canned stdout，并记录命令；测试从命令里抽出 base64 解码校验 config：

```python
class _FakeSandbox:
    def __init__(self, result=None):
        self.result = result or {"exit_status": "Submitted", "submission": "diff --git...", "model_stats": {}}
        self.exec_shell_calls: list[str] = []

    async def exec_shell(self, script, *, timeout=None, workdir=None, env=None):
        self.exec_shell_calls.append(script)
        return ExecResult(exit_code=0, stdout=json.dumps(self.result) + "\n", stderr="")
```

用例：
- 缺 `base_url` → `ValueError`（match `base_url`）。
- 无 user 消息 → `ValueError`（match `requires a 'user' message`）。
- happy path：恰好 1 次 `exec_shell`；命令含 `printf %s` + `base64 -d` + `/opt/mini-swe-agent/bin/python` + `run_agent.py`；解出 b64 后 `task_config["task"]`、`gateway_url == "http://127.0.0.1:38197/v1"`、`agent.step_limit` 正确；`AgentResult.output` 含 `exit_status/submission`；`finished is True`（Submitted）。
- stdout 非 JSON / 空 → `output["exit_status"] == "error"`、`finished is False`，不抛异常。
- 自定义 `step_limit`/`proxy_port` 生效。

---

## 4. 两个框架小改动

### 4.1 `run_task` 隧道注入（`uni_agent/framework/task_runner.py`）

**option A：端口放 task_config。** `sandbox.sandbox_kwargs.proxy_port` 是隧道端口的**唯一来源**（写死在 YAML，与老流程 `proxy_port=38197` 一致）；`run_task` 检测到该字段后只注入**运行时派生**的 `upstream`（`session.base_url` 的 host:port），并顺手把 `agent.proxy_port` 同步成同一值。**无新增 kwarg、无硬编码端口常量。**

```python
def _inject_gateway_tunnel(task: dict[str, Any], base_url: str) -> dict[str, Any]:
    """把网关地址注入沙箱 tunnel；agent 侧 proxy_port 与沙箱保持同一值。"""
    parsed = urlparse(base_url)
    if not parsed.hostname or not parsed.port:
        raise ValueError(f"cannot derive gateway tunnel upstream from base_url={base_url!r}")
    upstream = f"{parsed.hostname}:{parsed.port}"
    proxy_port = task["sandbox"]["sandbox_kwargs"]["proxy_port"]
    return _deep_merge(task, {
        "sandbox": {"sandbox_kwargs": {"upstream": upstream}},
        "agent": {"proxy_port": proxy_port},
    })
```

在 `resolver.resolve(...)` 之后、`get_task(task).run()` 之前：

```python
tunnel_port = (task.get("sandbox") or {}).get("sandbox_kwargs", {}).get("proxy_port")
if tunnel_port and session.base_url:
    task = _inject_gateway_tunnel(task, session.base_url)
```

说明：
- `upstream` 是唯一无法静态写死的运行时量（`session.base_url` 由框架动态分配），必须注入；其余 sandbox 配置（provider/mounts/proxy_port）全留在 task_config。
- 只要 YAML 里配了 `proxy_port`，注入就生效；没配则完全不碰，对其他 recipe 零影响。
- `_deep_merge` 复用 `uni_agent.tasks.config` 里的实现（同模块内）。
- `OpenyuanrongSandbox.start()` 已原生支持 `upstream`/`proxy_port`，沙箱本体零改动。
- 若某部署的网关 host:port 固定，也可直接在 task_config 写死 `sandbox_kwargs.upstream` 从而免掉此改动；但推荐保留（更贴近老流程的运行时派生）。

补充单测（`tests/uni_agent/framework/test_task_runner.py`）：
1. 配置了 `proxy_port` → `sandbox.sandbox_kwargs.upstream=="host:port"`、`agent.proxy_port` 同步为同一值。
2. 已存在 `sandbox_kwargs`/`mounts` → 深合并保留不覆盖。
3. `base_url` 缺 host/port → `ValueError`（match `base_url`）。

### 4.2 openyuanrong 镜像映射（`uni_agent/sandbox/openyuanrong.py`）

仿 `vefaas._to_vefaas_image`（老流程 image 是 parquet 全地址，新数据是 canonical 名，需要 provider 侧映射）：

```python
_OPENYUANRONG_REGISTRY = os.getenv("OPENYUANRONG_IMAGE_REGISTRY", "swr.cn-east-3.myhuaweicloud.com/openyuanrong")

def _to_openyuanrong_image(image: str) -> str:
    if image.startswith("swebench/") or image.startswith("swerebench/"):
        return f"{_OPENYUANRONG_REGISTRY}/{image}"   # ⚠ 具体集合名/tag 以 registry 实际为准
    return image                                     # 已是全地址（如 tool 镜像）原样透传
```

```python
@classmethod
def from_config(cls, config: SandboxConfig):
    return cls(image=_to_openyuanrong_image(config.image), runtime_timeout=config.runtime_timeout, **config.sandbox_kwargs)
```

> ⚠ **待确认**：`swebench/`/`swerebench/` 在 openyuanrong registry 的实际集合名与 tag。建议先拿 1 个样例镜像建沙箱 smoke 后再定稿映射表（或保持 env 可覆盖）。

---

## 5. task_config YAML

`examples/blackbox_recipes/mini_swe_agent/task_config_mini_swe_agent.yaml`：

```yaml
- name: swe_rebench
  sandbox:
    provider: openyuanrong
    runtime_timeout: 7200
    sandbox_kwargs:
      proxy_port: 38197
      mounts:
        - target: /opt/mini-swe-agent
          image_url: swr.cn-east-3.myhuaweicloud.com/openyuanrong/mini-swe-agent-tool:latest
  agent:
    name: mini_swe_agent
    step_limit: 100
    run_timeout: 7200
    model:
      temperature: 1.0
      top_p: 1.0
      max_total_tokens: 131072

- name: swe_bench
  sandbox:
    provider: openyuanrong
    runtime_timeout: 7200
    sandbox_kwargs:
      proxy_port: 38197
      mounts:
        - target: /opt/mini-swe-agent
          image_url: swr.cn-east-3.myhuaweicloud.com/openyuanrong/mini-swe-agent-tool:latest
  agent:
    name: mini_swe_agent
    step_limit: 100
    run_timeout: 7200
    model:
      temperature: 1.0
      top_p: 1.0
      max_total_tokens: 131072
```

说明：
- sample 行的 `sandbox.image`（canonical 名）深合并覆盖文件默认；`provider`/`mounts`/`runtime_timeout`/`proxy_port` 来自文件。
- `proxy_port` 在 YAML 写死（option A，唯一来源）；`run_task` 检测到它即注入 `upstream` 并同步 `agent.proxy_port`。
- `agent.model.base_url/api_key/model_name` 由 `run_task` 运行时注入，不写。
- tool 镜像名与老 recipe `DEFAULT_TOOL_IMAGE` 一致。

---

## 6. Dockerfile / build_tool.sh / run_agent.py —— 零改动

`Dockerfile.mini-swe-agent-tool`、`build_tool.sh`、`run_agent.py` **原样保留**（老代码即可用）：
- venv 仍在 `/opt/mini-swe-agent`，`FROM scratch` 打到镜像根，Mount 后落在 `/opt/mini-swe-agent/bin/python`。
- `run_agent.py` 由 Dockerfile `COPY run_agent.py /opt/mini-swe-agent/bin/run_agent.py`，因此作为构建源文件保留在 recipe 目录（属「必要脚本」）。

不需要任何重新构建/改造。

---

## 7. 数据准备

```bash
python -m uni_agent.tasks.swe_rebench.preprocess --local-save-dir ~/data/uni_agent
python -m uni_agent.tasks.swe_bench.preprocess     --local-save-dir ~/data/uni_agent
# 产出 swe_rebench_filtered.parquet / swe_bench_verified.parquet
```

每行带 `extra_info.tools_kwargs.task = {name, sandbox:{image: canonical}, prompt, metadata}`，直接供 `run_task`。老 parquet（`tools_kwargs.env` / `reward.registry`）废弃。

---

## 8. run_train.sh 接线（diff 式要点）

参照 `examples/quickstart/training/train_qwen3_moe.sh` 的 CLI 覆盖风格：

```bash
++actor_rollout_ref.rollout.agent.agent_loop_manager_class=uni_agent.framework.entry.AgentFrameworkRolloutAdapter \
++actor_rollout_ref.rollout.custom.agent_framework.gateway_count=${GATEWAY_COUNT} \
++actor_rollout_ref.rollout.custom.agent_framework.log_dir=${AGENT_LOG_DIR} \
++actor_rollout_ref.rollout.custom.agent_framework.agent_runners.task.runner_fqn=uni_agent.framework.task_runner.run_task \
++actor_rollout_ref.rollout.custom.agent_framework.agent_runners.task.dispatch_mode=ray_task \
++actor_rollout_ref.rollout.custom.agent_framework.agent_runners.task.max_concurrent_sessions=${CONCURRENCY} \
++actor_rollout_ref.rollout.custom.agent_framework.agent_runners.task.runner_kwargs.task_config_path=${TASK_CONFIG} \
++actor_rollout_ref.rollout.custom.agent_framework.agent_runners.task.runner_kwargs.model_name=${SERVED_MODEL_NAME} \
++actor_rollout_ref.rollout.custom.agent_framework.agent_runners.task.runner_kwargs.report_reward=True \
++actor_rollout_ref.rollout.custom.agent_framework.use_reward_loop_worker=False \
++actor_rollout_ref.rollout.multi_turn.enable=True \
++actor_rollout_ref.rollout.multi_turn.max_parallel_calls=1 \
++actor_rollout_ref.rollout.multi_turn.format=${TOOL_PARSER} \
...（actor/rollout/val 等沿用 quickstart）
```

env：`OPENYUANRONG_SERVER_ADDRESS` / `OPENYUANRONG_TOKEN`（provider 必需）、`SANDBOX_PROVIDER=openyuanrong`、`OPENYUANRONG_IMAGE_REGISTRY`（可选）、数据路径、`SERVED_MODEL_NAME`、`CONCURRENCY` 等。

---

## 9. recipe 最终目录

```
examples/blackbox_recipes/mini_swe_agent/
├── README.md                              # 重写：架构、env、构建/训练/推理步骤
├── task_config_mini_swe_agent.yaml        # 新增（§5）
├── run_train.sh                           # 改接线（§8）
├── Dockerfile.mini-swe-agent-tool         # 原样
├── build_tool.sh                          # 原样
└── run_agent.py                           # 原样（Dockerfile COPY 依赖，必要脚本）
```
删除：`mini_swe_agent_runner.py`、`reward.py`、`dataset.py`、`parallel_infer.py`、`config/`、`__init__.py`、`run_infer.sh`。

---

## 10. 老参数 → 新配置映射

| 老（env / runner） | 新 |
|---|---|
| `AGENT_MAX_TURNS` | task_config `agent.step_limit` |
| `SWE_AGENT_RUN_TIMEOUT` | `agent.run_timeout` |
| `SWE_AGENT_TOOL_IMAGE` | `sandbox.sandbox_kwargs.mounts[].image_url` |
| `CONDA_ENV` | `agent.conda_env` |
| runner 内 `rewrite_gateway_url(gateway_url)` | agent 内 `rewrite_gateway_url(base_url, proxy_port)` |
| runner 内 `upstream=extract_upstream(gateway_url)` + `proxy_port=38197` | `run_task` 注入 `upstream`；`proxy_port` 写死 task_config `sandbox_kwargs.proxy_port` |
| parquet `env.image`（全地址） | preprocess canonical 名 → `_to_openyuanrong_image` |
| parquet `reward.metadata` | `metadata`（同字段） |
| recipe `reward.py` / `evaluate_in_env` | `uni_agent/tasks/swe_rebench/reward.py`（Task 内） |
| `POST reward_info` | `run_task` `report_reward=True` |

---

## 11. 实现顺序

1. `uni_agent/agents/mini_swe_agent/agent.py`：按 §3 实现（老代码搬移），重写测试并跑通。
2. `uni_agent/sandbox/openyuanrong.py`：加 `_to_openyuanrong_image` + from_config 应用（补映射单测）。
3. `uni_agent/framework/task_runner.py`：加隧道 `upstream` 注入（读 task_config `proxy_port`，补单测）。
4. task_config YAML；验证 tool 镜像已就绪（无需重建）。
5. 重跑两个 preprocess 出数据。
6. 重写 run_train.sh / README；小批量 smoke 端到端。
7. 清理 recipe 旧文件。

## 12. 风险与待确认

1. **openyuanrong registry 实际镜像命名**：`swebench/`/`swerebench/` → 集合名+tag 需照 registry 核对后定稿 `_to_openyuanrong_image`（建议样例镜像建沙箱 smoke）。
2. **隧道端口/并发**：`proxy_port=38197` 沿用老流程，现写死 task_config `sandbox_kwargs.proxy_port`；多沙箱并发时 SDK 是否复用/需分段端口需验证。
3. **`run_agent.py` 依赖的 mini-swe-agent 版本**：镜像内 2.2.8 已固定，契约（stdin/stdout）不变，无版本风险。
4. **swe_rebench 数据字段差异**：metadata 含 `FAIL_TO_FAIL`/`PASS_TO_FAIL`/`log_parser`，reward 按行读取，recipe 无需改。

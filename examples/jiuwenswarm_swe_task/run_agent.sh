#!/usr/bin/env bash
# In-sandbox entry for the jiuwenswarm sidecar tool image.
#
# Reads the task prompt from **stdin** (piped in by the host via base64),
# configures the jiuwenswarm gateway to point at the reverse-tunneled policy
# endpoint (JWS_API_BASE), launches jiuwenswarm-app if needed, runs the
# jiuwenswarm CLI in code.normal mode against the project dir, and passes the
# CLI's --json output through to stdout (always exiting 0 — the sandbox masks
# non-zero exits to 127 and drops stdout).
#
# Env vars (set by the host agent's build_agent_command):
#   JWS_API_BASE      — gateway URL (http://127.0.0.1:<proxy_port>/sessions/.../v1)
#   JWS_MODEL_NAME    — model name sent to the endpoint (default: unknown)
#   JWS_API_KEY       — API key (default: EMPTY)
#   JWS_PROJECT_DIR   — the repo working dir (host's workdir, e.g. /testbed);
#                       falls back to $PWD
#
# Wallclock timeout is enforced host-side (sandbox.exec_shell(run_timeout)),
# so this script never sees a timeout signal.

set -uo pipefail

TOOL_BIN="/opt/jiuwenswarm/bin"
export PATH="${PATH}:${TOOL_BIN}"
JWS_WS="${JIUWENSWARM_DATA_DIR:-$HOME/.jiuwenswarm}"
ENV_FILE="$JWS_WS/config/.env"
GATEWAY_PORT="${GATEWAY_PORT:-19001}"

# Read task prompt from stdin (piped by the host agent)
TASK="$(cat)"

# Validate required env
: "${JWS_API_BASE:?missing gateway_url}"

# Resolve project dir from the host's workdir (fallback: current dir)
PROJECT_DIR="${JWS_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR"

# Initialize workspace (idempotent)
echo "" | jiuwenswarm-init >/dev/null 2>&1 || true
mkdir -p "$JWS_WS/logs"

# Patch the generated config and the package template. Unlike v11 this leaves the
# stock tool/skill/subagent surface alone; only these four settings change, so the
# per-turn system prompt stays at its shipped size. -P keeps the cwd off sys.path,
# where a task repo could shadow one of the sidecar's own dependencies.
"$TOOL_BIN/python3.12" -P - "$JWS_WS/config/config.yaml" <<'PY' || echo "[warn] config patch failed" >&2
import importlib.util, os, sys
from ruamel.yaml import YAML

root = os.path.dirname(importlib.util.find_spec("jiuwenswarm").origin)

def patch(cfg):
    # null probes the endpoint with a synthetic image per (api_base, model_name);
    # that probe's image trajectory breaks the rollout.
    cfg["react"]["enable_read_image_multimodal"] = False
    # The task loop forces sys.maxsize as the ReAct budget, so it must be off for
    # max_iterations to mean anything.
    cfg["react"]["enable_task_loop"] = False
    cfg["react"]["max_iterations"] = 150
    # Compression makes its own model calls, each a separate trajectory. Keeping
    # every message in the protected tail leaves an empty span, so it never fires.
    context_engine = cfg["react"]["context_engine_config"]
    for key in ("current_round_compressor_config", "round_level_compressor_config"):
        section = context_engine.get(key)
        if isinstance(section, dict):
            section["keep_recent_messages"] = 1_000_000_000

yaml = YAML()
yaml.preserve_quotes = True
yaml.default_flow_style = False
yaml.indent(mapping=2, sequence=4, offset=2)
yaml.width = 4096

for path in (sys.argv[1], os.path.join(root, "resources", "config.yaml")):
    if not os.path.isfile(path) or not os.access(path, os.W_OK):
        continue
    with open(path, encoding="utf-8") as f:
        cfg = yaml.load(f)
    patch(cfg)
    tmp = path + ".patch.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f)
    os.replace(tmp, path)
PY

# Write .env for jiuwenswarm-app
cat > "$ENV_FILE" <<EOF
MODEL_PROVIDER=Anthropic
API_BASE=${JWS_API_BASE}
MODEL_NAME=${JWS_MODEL_NAME:-unknown}
API_KEY=${JWS_API_KEY:-EMPTY}
# The gateway's default budget is ~60s (240 attempts at a 0.25s local cap); a
# contended AgentServer misses it and the gateway exits before binding its port.
# 2400 attempts is ~605s, inside the 660s waited below.
AGENT_CONNECT_RETRY=2400
EOF

# Start gateway if not already listening. app.py spawns its children with -m, so
# the process cwd lands on sys.path; leaving the repo for the workspace keeps a
# task repo from shadowing a sidecar dependency at import time.
if ! (exec 3<>"/dev/tcp/127.0.0.1/$GATEWAY_PORT") 2>/dev/null; then
  ( cd "$JWS_WS" && nohup jiuwenswarm-app >"$JWS_WS/logs/startup.log" 2>&1 & )
  for i in $(seq 1 660); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$GATEWAY_PORT") 2>/dev/null; then
      echo "[ok] gateway ready on :$GATEWAY_PORT" >&2
      break
    fi
    if [ "$i" -eq 660 ]; then
      echo "[error] gateway timeout; startup.log tail:" >&2
      tail -40 "$JWS_WS/logs/startup.log" >&2
      exit 3
    fi
    sleep 1
  done
fi

# Run CLI — pass through its --json output when it carries "ok"; otherwise emit a
# minimal failure JSON so stdout always carries exactly one result. Always exit 0
# (a non-zero exit would be masked to 127 and stdout dropped).
CLI_OUT="$(printf '%s' "$TASK" | jiuwenswarm --mode code.normal --json \
  --cwd "$PROJECT_DIR" --trusted-dir "$PROJECT_DIR")"
CLI_RC=$?
# The sidecar's own python: the task image need not ship python3, and -P keeps
# the repo from shadowing json.
if printf '%s' "$CLI_OUT" | "$TOOL_BIN/python3.12" -P -c 'import json,sys; d=json.load(sys.stdin); assert "ok" in d' 2>/dev/null; then
  printf '%s\n' "$CLI_OUT"
else
  printf '%s\n' "{\"exit_status\": \"error\", \"content\": \"\", \"error\": \"CLI failed rc=${CLI_RC}\"}"
fi
exit 0

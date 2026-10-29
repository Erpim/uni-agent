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

# Trim the generated config (and the package template) to shrink the system prompt
"$TOOL_BIN/python3.12" - "$JWS_WS/config/config.yaml" <<'PY' || echo "[warn] config trim failed" >&2
import importlib.util, os, sys
from ruamel.yaml import YAML

root = os.path.dirname(importlib.util.find_spec("jiuwenswarm").origin)
version = None
with open(os.path.join(root, "common", "_build_config.py"), encoding="utf-8") as f:
    for line in f:
        if line.startswith("VERSION"):
            version = line.split("=", 1)[1].strip().strip("'\"")
            break

def trim(cfg):
    cfg["auto_recap"]["enabled"] = False
    cfg["symphony"]["skill_retrieval"]["enabled"] = False
    cfg["react"]["skill_mode"] = "auto_list"
    cfg["react"]["enable_task_loop"] = False
    cfg["react"]["enable_read_image_multimodal"] = False
    cfg["react"]["subagent_runtime"]["enabled"] = False
    subs = cfg["react"]["subagents"]
    for name in ("statusline-setup", "general_agent", "browser_agent"):
        subs[name]["enabled"] = False
    subs["explore_agent"] = {"enabled": False}
    subs["plan_agent"] = {"enabled": False}
    cfg["modes"]["code"]["memory"]["enabled"] = False
    cfg["modes"]["code"]["tools"] = []
    if version:
        cfg["config_version"] = version

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
    trim(cfg)
    tmp = path + ".trim.tmp"
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
EOF

# Start gateway if not already listening
if ! (exec 3<>"/dev/tcp/127.0.0.1/$GATEWAY_PORT") 2>/dev/null; then
  nohup jiuwenswarm-app >"$JWS_WS/logs/startup.log" 2>&1 &
  for i in $(seq 1 180); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$GATEWAY_PORT") 2>/dev/null; then
      echo "[ok] gateway ready on :$GATEWAY_PORT" >&2
      break
    fi
    [ "$i" -eq 180 ] && { echo "[error] gateway timeout" >&2; exit 3; }
    sleep 1
  done
fi

# Run CLI — pass through its --json output when it carries "ok"; otherwise emit a
# minimal failure JSON so stdout always carries exactly one result. Always exit 0
# (a non-zero exit would be masked to 127 and stdout dropped).
CLI_OUT="$(printf '%s' "$TASK" | jiuwenswarm --mode code.normal --json \
  --cwd "$PROJECT_DIR" --trusted-dir "$PROJECT_DIR")"
CLI_RC=$?
if printf '%s' "$CLI_OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert "ok" in d' 2>/dev/null; then
  printf '%s\n' "$CLI_OUT"
else
  printf '%s\n' "{\"exit_status\": \"error\", \"content\": \"\", \"error\": \"CLI failed rc=${CLI_RC}\"}"
fi
exit 0
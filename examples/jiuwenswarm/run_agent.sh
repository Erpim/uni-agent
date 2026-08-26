#!/usr/bin/env bash
# In-sandbox entry for the jiuwenswarm sidecar tool image.
#
# Reads the task prompt from **stdin** (piped in by the host via base64),
# configures the jiuwenswarm gateway to point at the reverse-tunneled policy
# endpoint (JWS_API_BASE), launches jiuwenswarm-app if needed, runs the
# jiuwenswarm CLI in code.normal mode against the project dir, and passes the
# CLI's --json output straight through to stdout.
#
# Env vars (set by the host agent's build_agent_command):
#   JWS_API_BASE      — gateway URL (http://127.0.0.1:<proxy_port>/sessions/.../v1)
#   JWS_MODEL_NAME    — model name sent to the endpoint (default: openai/default)
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

# Write .env for jiuwenswarm-app
cat > "$ENV_FILE" <<EOF
MODEL_PROVIDER=OpenAI
API_BASE=${JWS_API_BASE}
MODEL_NAME=${JWS_MODEL_NAME:-openai/default}
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

# Run CLI — --json output passes straight through to stdout
printf '%s' "$TASK" | jiuwenswarm --mode code.normal --json \
  --cwd "$PROJECT_DIR" --trusted-dir "$PROJECT_DIR"
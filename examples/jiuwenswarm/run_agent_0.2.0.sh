#!/usr/bin/env bash
set -uo pipefail

TOOL_BIN="/opt/jiuwenswarm/bin"
export PATH="${PATH}:${TOOL_BIN}"
JWS_WS="${JIUWENSWARM_DATA_DIR:-$HOME/.jiuwenswarm}"
ENV_FILE="$JWS_WS/config/.env"
GATEWAY_PORT="${GATEWAY_PORT:-19001}"
GATEWAY_URL="${GATEWAY_URL:-ws://127.0.0.1:${GATEWAY_PORT}/tui}"
DRIVER_TIMEOUT="${JWS_DRIVER_TIMEOUT:-3300}"

TASK="$(cat)"

: "${JWS_API_BASE:?missing gateway_url}"

PROJECT_DIR="${JWS_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR"

if [ -d "$JWS_WS" ]; then
  printf 'yes\n1\n' | jiuwenswarm-init >/dev/null 2>&1 || true
else
  printf '1\n' | jiuwenswarm-init >/dev/null 2>&1 || true
fi
mkdir -p "$JWS_WS/config"
mkdir -p "$JWS_WS/logs"

cat > "$ENV_FILE" <<EOF
MODEL_PROVIDER=OpenAI
API_BASE=${JWS_API_BASE}
MODEL_NAME=${JWS_MODEL_NAME:-openai/default}
API_KEY=${JWS_API_KEY:-EMPTY}
EOF

export MODEL_PROVIDER=OpenAI
export API_BASE="${JWS_API_BASE}"
export MODEL_NAME="${JWS_MODEL_NAME:-openai/default}"
export API_KEY="${JWS_API_KEY:-EMPTY}"

STARTED_APP=0
if ! (exec 3<>"/dev/tcp/127.0.0.1/$GATEWAY_PORT") 2>/dev/null; then
  nohup jiuwenswarm-app >"$JWS_WS/logs/startup.log" 2>&1 &
  APP_PID=$!
  STARTED_APP=1
  for i in $(seq 1 300); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$GATEWAY_PORT") 2>/dev/null; then
      break
    fi
    [ "$i" -ge 300 ] && { echo "[error] gateway timeout" >&2; exit 3; }
    sleep 1
  done
  sleep 8
fi

DRIVER_PY="$JWS_WS/tui_driver.py"
cat > "$DRIVER_PY" <<'PY'
import argparse, asyncio, json, os, sys, time

async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--gateway-url", default="ws://127.0.0.1:19001/tui")
    p.add_argument("--session-id", default="tui_swe")
    p.add_argument("--cwd", default="")
    p.add_argument("--timeout", type=float, default=3300.0)
    a = p.parse_args()
    task = sys.stdin.read()

    ws_dir = os.environ.get("JIUWENSWARM_DATA_DIR")
    base = ws_dir if ws_dir else os.path.join(os.path.expanduser("~"), ".jiuwenswarm")
    logs_dir = os.path.join(base, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    ev_path = os.path.join(logs_dir, "events_%s.jsonl" % a.session_id)
    ev_file = open(ev_path, "a")

    text = []
    tool_ev = []
    final = None
    done = False
    reason = "timeout"
    deadline = time.monotonic() + a.timeout

    try:
        import websockets
        async with websockets.connect(a.gateway_url, max_size=None, open_timeout=30) as ws:
            try:
                await asyncio.wait_for(ws.recv(), timeout=20)
            except Exception:
                pass
            params = {"content": task, "session_id": a.session_id, "mode": "code.normal"}
            if a.cwd:
                params["cwd"] = a.cwd
            req = {"type": "req", "id": a.session_id, "method": "chat.send",
                   "params": params, "is_stream": True}
            await ws.send(json.dumps(req, ensure_ascii=False))
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
                except asyncio.TimeoutError:
                    break
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                t = d.get("type")
                if t == "res":
                    pl = d.get("payload") or {}
                    if isinstance(pl, dict) and pl.get("accepted"):
                        continue
                    if isinstance(pl, dict) and pl.get("content"):
                        text.append(str(pl["content"]))
                    if d.get("ok"):
                        done = True
                        reason = "res"
                    break
                if t == "event":
                    ev = d.get("event")
                    pl = d.get("payload") or {}
                    ev_file.write(json.dumps({"t": time.time(), "event": ev, "payload": pl}, ensure_ascii=False) + "\n")
                    if ev == "chat.delta" and isinstance(pl, dict) and pl.get("content"):
                        text.append(str(pl["content"]))
                    elif ev == "chat.final":
                        if isinstance(pl, dict) and pl.get("event_type") == "keepalive":
                            continue
                        if isinstance(pl, dict) and pl.get("content"):
                            final = str(pl["content"])
                        done = True
                        reason = "chat.final"
                        break
                    elif ev == "chat.processing_status" and isinstance(pl, dict) and pl.get("is_complete"):
                        done = True
                        reason = "complete"
                        break
                    elif ev in ("chat.tool_call", "chat.tool_update", "chat.tool_result") and isinstance(pl, dict):
                        tool_ev.append({ev: {k: v for k, v in pl.items() if k != "session_id"}})
    except Exception as exc:
        reason = "error:" + str(exc)[:200]
    ev_file.close()

    content = final if final is not None else "".join(text)
    ok = done and bool(content)
    out = {"ok": ok, "content": content, "session_id": a.session_id, "event": reason}
    if not ok:
        out["error"] = ("no chat.final" if done else "no terminal event") + " (reason=%s)" % reason
    if tool_ev:
        out["tool_calls"] = tool_ev[:200]
    print(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()

asyncio.run(main())
PY

SID="tui_swe_$(date +%s%N)"
RESULT_JSON=""
for attempt in 1 2 3; do
  RESULT_JSON="$(printf '%s' "$TASK" | "$TOOL_BIN/python" "$DRIVER_PY" \
    --gateway-url "$GATEWAY_URL" --session-id "$SID" --cwd "$PROJECT_DIR" \
    --timeout "$DRIVER_TIMEOUT" 2>"$JWS_WS/logs/driver.err")"
  if printf '%s' "$RESULT_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert "ok" in d' 2>/dev/null; then
    break
  fi
  echo "[warn] driver attempt $attempt failed; retrying" >&2
  RESULT_JSON=""
  [ "$attempt" -lt 3 ] && sleep 10
done

if [ -n "$RESULT_JSON" ]; then
  printf '%s\n' "$RESULT_JSON"
else
  printf '%s\n' "{\"exit_status\": \"error\", \"ok\": false, \"content\": \"\", \"error\": \"driver failed after 3 attempts\"}"
fi

if [ "$STARTED_APP" = 1 ]; then
  kill "$APP_PID" 2>/dev/null || true
  sleep 1
  pkill -TERM -P "$APP_PID" 2>/dev/null || true
fi
exit 0
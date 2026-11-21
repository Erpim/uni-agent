#!/usr/bin/env bash
# Inference for the jiuwenswarm blackbox recipe.
#
# Usage:
#   bash examples/jiuwenswarm_swe_task/run_infer_jiuwenswarm.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
cd "${REPO_ROOT}"

export DEPLOYMENT="${DEPLOYMENT:-agent_dx}"
export ADX_SERVER_ADDRESS="${ADX_SERVER_ADDRESS:?ADX_SERVER_ADDRESS must be set}"
export ADX_TOKEN="${ADX_TOKEN:?ADX_TOKEN must be set}"
export ADX_TLS="${ADX_TLS:-1}"
export ADX_TLS_VERIFY="${ADX_TLS_VERIFY:-1}"
export ADX_GATEWAY_TLS="${ADX_GATEWAY_TLS:-1}"
export ADX_TUNNEL_SSL_VERIFY="${ADX_TUNNEL_SSL_VERIFY:-1}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0,1}"

DATA_PATH="${DATA_PATH:-/swe_bench_verified.parquet}"
MODEL_PATH="${MODEL_PATH:-/models/Qwen/Qwen3.5-9B}"
TASK_CONFIG="${TASK_CONFIG:-examples/jiuwenswarm_swe_task/task_config_jiuwenswarm.yaml}"
TOOL_PARSER="${TOOL_PARSER:-qwen3_coder}"
TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-2}"
NNODES="${NNODES:-1}"
N_GPUS_PER_NODE="${N_GPUS_PER_NODE:-2}"
LIMIT="${LIMIT:-1}"
N="${N:-1}"
CONCURRENCY="${CONCURRENCY:-1}"
LOG_DIR="${LOG_DIR:-/tmp/uni_agent_logs}"
RESULT_PATH="${RESULT_PATH:-/tmp/jiuwenswarm_infer_result.json}"

# `ray job submit` does not forward this shell's env into the job driver, so
# the job's runtime env is spelled out here and passed via --runtime-env-json.
ray job submit \
    --runtime-env-json "$(cat <<JSON
{
  "env_vars": {
    "PYTHONPATH": "verl",
    "DEPLOYMENT": "${DEPLOYMENT}",
    "ADX_SERVER_ADDRESS": "${ADX_SERVER_ADDRESS}",
    "ADX_TOKEN": "${ADX_TOKEN}",
    "ADX_TLS": "${ADX_TLS}",
    "ADX_TLS_VERIFY": "${ADX_TLS_VERIFY}",
    "ADX_GATEWAY_TLS": "${ADX_GATEWAY_TLS}",
    "ADX_TUNNEL_SSL_VERIFY": "${ADX_TUNNEL_SSL_VERIFY}",
    "CUDA_VISIBLE_DEVICES": "${CUDA_VISIBLE_DEVICES}"
  },
  "excludes": ["/.git/"]
}
JSON
)" \
    --working-dir . \
    -- python3 examples/inference/parallel_infer_verl.py \
    --data-path "${DATA_PATH}" \
    --model-path "${MODEL_PATH}" \
    --task-config "${TASK_CONFIG}" \
    --tool-parser "${TOOL_PARSER}" \
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
    --nnodes "${NNODES}" \
    --n-gpus-per-node "${N_GPUS_PER_NODE}" \
    --limit "${LIMIT}" \
    --n "${N}" \
    --concurrency "${CONCURRENCY}" \
    --language-model-only \
    --log-dir "${LOG_DIR}" \
    --result-path "${RESULT_PATH}"

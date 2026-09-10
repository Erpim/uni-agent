#!/usr/bin/env bash
set -euo pipefail

export DEPLOYMENT="openyuanrong"
export OPENYUANRONG_SERVER_ADDRESS="${OPENYUANRONG_SERVER_ADDRESS:?OPENYUANRONG_SERVER_ADDRESS must be set}"
export OPENYUANRONG_TOKEN="${OPENYUANRONG_TOKEN:?OPENYUANRONG_TOKEN must be set}"
export TUNNEL_SSL_VERIFY="0"
export CUDA_VISIBLE_DEVICES="2,3"

# `ray job submit` does not forward this shell's env into the job driver, so
# the job's runtime env is spelled out here and passed via --runtime-env-json.
ray job submit --no-wait \
    --runtime-env-json "$(cat <<JSON
{
  "env_vars": {
    "PYTHONPATH": "verl",
    "TORCH_NCCL_AVOID_RECORD_STREAMS": "1",
    "CUDA_DEVICE_MAX_CONNECTIONS": "1",
    "VLLM_DISABLE_COMPILE_CACHE": "1",
    "DEPLOYMENT": "${DEPLOYMENT}",
    "OPENYUANRONG_SERVER_ADDRESS": "${OPENYUANRONG_SERVER_ADDRESS}",
    "OPENYUANRONG_TOKEN": "${OPENYUANRONG_TOKEN}",
    "TUNNEL_SSL_VERIFY": "${TUNNEL_SSL_VERIFY}",
    "CUDA_VISIBLE_DEVICES": "${CUDA_VISIBLE_DEVICES}"
  },
  "excludes": ["/.git/"]
}
JSON
)" \
    --working-dir . \
    -- python3 examples/inference/parallel_infer_verl.py \
    --data-path /home/dyp/recipe/swe_bench_verified.parquet \
    --model-path /data1/models/Qwen/Qwen3.5-4B \
    --task-config examples/jiuwenswarm/task_config_jiuwenswarm.yaml \
    --tool-parser qwen3_coder \
    --tensor-parallel-size 2 \
    --nnodes 1 \
    --n-gpus-per-node 2 \
    --limit 1 \
    --log-dir /tmp/uni_agent_logs \
    --concurrency 1

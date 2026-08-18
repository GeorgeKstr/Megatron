#!/usr/bin/env bash
# Megatron — Launch script
set -euo pipefail

cd "$(dirname "$0")"

# Defaults (override via environment or .env file)
export MEGATRON_HOST="${MEGATRON_HOST:-0.0.0.0}"
export MEGATRON_PORT="${MEGATRON_PORT:-8080}"
export LMSTUDIO_URL="${LMSTUDIO_URL:-http://localhost:1234/v1}"

# Load .env if present
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

echo "══════════════════════════════════════"
echo "  ⚡ Megatron — PC Remote Control"
echo "  Listening on  http://${MEGATRON_HOST}:${MEGATRON_PORT}"
echo "  LM Studio →    ${LMSTUDIO_URL}"
echo "══════════════════════════════════════"
echo ""

exec python server.py

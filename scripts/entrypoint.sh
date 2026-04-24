#!/usr/bin/env sh
set -eu

print_error() {
  printf '%s\n' "[dgrag][error] $*" >&2
}

print_info() {
  printf '%s\n' "[dgrag][info] $*"
}

require_env() {
  var_name="$1"
  hint="$2"
  value="$(printenv "$var_name" 2>/dev/null || true)"
  if [ -z "$value" ]; then
    MISSING_VARS="${MISSING_VARS}${MISSING_VARS:+, }${var_name}"
    if [ -n "$hint" ]; then
      MISSING_HINTS="${MISSING_HINTS}\n  - ${var_name}: ${hint}"
    else
      MISSING_HINTS="${MISSING_HINTS}\n  - ${var_name}"
    fi
  fi
}

MODE="${DGRAG_MODE:-webhook}"
HOST="${DGRAG_HOST:-0.0.0.0}"
PORT="${DGRAG_PORT:-8000}"
CACHE_DIR="${DGRAG_CACHE_DIR:-/app/.cache/dgrag}"

mkdir -p "$CACHE_DIR"

MISSING_VARS=""
MISSING_HINTS=""

case "$MODE" in
  webhook)
    require_env "GITHUB_TOKEN" "GitHub access token used to fetch PR metadata and post review comments."
    require_env "GITHUB_WEBHOOK_SECRET" "Webhook HMAC secret used to validate X-Hub-Signature-256."
    ;;
  cli)
    require_env "GITHUB_TOKEN" "GitHub access token required for PR review commands against GitHub URLs."
    ;;
  llm|full-review)
    require_env "GITHUB_TOKEN" "GitHub access token used to fetch PR metadata and post review comments."
    require_env "OPENAI_API_KEY" "Required only when running full-review / LLM-backed flows."
    ;;
  *)
    print_info "Unknown DGRAG_MODE='$MODE'; applying conservative validation."
    require_env "GITHUB_TOKEN" "GitHub access token required for GitHub-backed review flows."
    ;;
esac

if [ -n "$MISSING_VARS" ]; then
  print_error "Missing required environment variables: $MISSING_VARS"
  print_error "Set them in your container environment or .env file before starting D-GRAG."
  printf '%b\n' "$MISSING_HINTS" >&2
  print_error "Examples:"
  print_error "  - docker run --env-file .env btp-dgrag:latest"
  print_error "  - docker compose up --build"
  exit 1
fi

if [ "$#" -eq 0 ]; then
  set -- python -m uvicorn src.webhook:app --host "$HOST" --port "$PORT"
fi

if [ "$1" = "python" ] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "uvicorn" ]; then
  print_info "Starting D-GRAG webhook service on ${HOST}:${PORT}"
elif [ "$1" = "python" ] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "dgrag" ]; then
  print_info "Starting D-GRAG CLI command: $*"
else
  print_info "Executing command: $*"
fi

exec "$@"

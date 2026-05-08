#!/usr/bin/env sh
set -eu

IMAGE_NAME="${DGRAG_DOCKER_IMAGE:-btp-dgrag:test}"
ENV_FILE="${DGRAG_ENV_FILE:-.env}"
EXTRA_BUILD_ARGS="${DGRAG_DOCKER_BUILD_ARGS:-}"
EXTRA_RUN_ARGS="${DGRAG_DOCKER_RUN_ARGS:-}"

log() {
  printf '%s\n' "[test_docker_build] $*"
}

fail() {
  printf '%s\n' "[test_docker_build][error] $*" >&2
  exit 1
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "Required command not found: $1"
  fi
}

run_build() {
  if [ -n "$EXTRA_BUILD_ARGS" ]; then
    # shellcheck disable=SC2086
    docker build $EXTRA_BUILD_ARGS -t "$IMAGE_NAME" .
  else
    docker build -t "$IMAGE_NAME" .
  fi
}

run_help() {
  if [ -f "$ENV_FILE" ]; then
    if [ -n "$EXTRA_RUN_ARGS" ]; then
      # shellcheck disable=SC2086
      docker run --rm --env-file "$ENV_FILE" -e DGRAG_MODE=cli $EXTRA_RUN_ARGS "$IMAGE_NAME" python -m dgrag --help
    else
      docker run --rm --env-file "$ENV_FILE" -e DGRAG_MODE=cli "$IMAGE_NAME" python -m dgrag --help
    fi
  else
    log "Environment file '$ENV_FILE' not found; using minimal fallback env for CLI smoke test."
    if [ -n "$EXTRA_RUN_ARGS" ]; then
      # shellcheck disable=SC2086
      docker run --rm -e DGRAG_MODE=cli -e GITHUB_TOKEN=dummy-token $EXTRA_RUN_ARGS "$IMAGE_NAME" python -m dgrag --help
    else
      docker run --rm -e DGRAG_MODE=cli -e GITHUB_TOKEN=dummy-token "$IMAGE_NAME" python -m dgrag --help
    fi
  fi
}

main() {
  require_cmd docker

  log "Building Docker image: $IMAGE_NAME"
  run_build

  log "Running CLI smoke test: python -m dgrag --help"
  help_output="$(run_help 2>&1)" || {
    printf '%s\n' "$help_output" >&2
    fail "Docker CLI smoke test failed"
  }

  printf '%s\n' "$help_output"

  echo "$help_output" | grep -qi "Delta-GRAG CLI" || {
    fail "Expected CLI help output was not found"
  }

  echo "$help_output" | grep -qi "review" || {
    fail "Expected 'review' command to appear in CLI help output"
  }

  log "Docker build and CLI smoke test passed."
}

main "$@"

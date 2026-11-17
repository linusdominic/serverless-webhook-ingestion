#!/usr/bin/env bash
# Refuse to run anything unless every AWS call is certain to hit the local emulator.
# Sourced by the Makefile's shell for every target that talks to "AWS".
set -euo pipefail

fail() { echo "preflight: $*" >&2; exit 1; }

endpoint="${WEBHOOK_ENDPOINT_URL:-}"
[[ "$endpoint" =~ ^http://(localhost|127\.0\.0\.1):[0-9]{2,5}$ ]] \
  || fail "WEBHOOK_ENDPOINT_URL must be http://localhost:<port> or http://127.0.0.1:<port>, got '${endpoint}'"

[[ "${AWS_ACCESS_KEY_ID:-}" == "test" && "${AWS_SECRET_ACCESS_KEY:-}" == "test" ]] \
  || fail "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must both be the literal string 'test'"

[[ -z "${AWS_PROFILE:-}" && -z "${AWS_DEFAULT_PROFILE:-}" && -z "${AWS_SESSION_TOKEN:-}" && -z "${AWS_ROLE_ARN:-}" ]] \
  || fail "AWS_PROFILE, AWS_DEFAULT_PROFILE, AWS_SESSION_TOKEN and AWS_ROLE_ARN must be unset"

for var in AWS_SHARED_CREDENTIALS_FILE AWS_CONFIG_FILE; do
  file="${!var:-}"
  [[ -n "$file" && -f "$file" && ! -s "$file" ]] || fail "$var must point at an existing empty file, got '${file}'"
done

[[ "${AWS_ENDPOINT_URL:-}" == "$endpoint" ]] || fail "AWS_ENDPOINT_URL must equal WEBHOOK_ENDPOINT_URL"
[[ "${AWS_EC2_METADATA_DISABLED:-}" == "true" ]] || fail "AWS_EC2_METADATA_DISABLED must be true"

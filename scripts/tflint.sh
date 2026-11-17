#!/usr/bin/env bash
# tflint over every module and both roots, from a pinned image.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
image="ghcr.io/terraform-linters/tflint:v0.66.0"

# GITHUB_TOKEN, when set, is passed through so that fetching the AWS ruleset is not
# rate limited in CI. It is not needed locally.
run() {
  docker run --rm -e GITHUB_TOKEN \
    -v "$root:/workspace" -v webhook-ingestion-tflint-plugins:/root/.tflint.d \
    -w /workspace/infra "$image" "$@"
}

run --init --config /workspace/.tflint.hcl >/dev/null
run --recursive --config /workspace/.tflint.hcl --format compact
echo "tflint: no findings"

#!/usr/bin/env bash
# Static security scan of the Terraform with checkov, pinned. Runs offline: nothing is
# downloaded from or uploaded to the scanner's vendor.
#
# The scan must come back with zero failed checks. A finding is either fixed or
# suppressed where it occurs with a `checkov:skip=<id>: <reason>` comment; every
# suppression is listed with its reason in docs/security-scan.md.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

uvx --quiet --python 3.12 checkov==3.4.5 \
  --directory infra \
  --framework terraform \
  --download-external-modules false \
  --skip-download \
  --skip-path '\.terraform' \
  --quiet --compact "$@"

#!/usr/bin/env bash
# The emulator copies large function packages (compactor.zip) into Docker volumes of
# its own and leaves them behind when it stops. Remove the ones this stack created.
# Only volumes whose name starts with the emulator's prefix for this project's
# functions are touched.
set -euo pipefail

prefix="floci-aws-code-webhook-ingestion-"

docker volume ls --quiet --filter "name=${prefix}" | while read -r volume; do
  case "$volume" in
    "${prefix}"*) docker volume rm "$volume" >/dev/null ;;
  esac
done

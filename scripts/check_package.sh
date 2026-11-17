#!/usr/bin/env bash
# Import the built compactor package inside the Lambda base image and round-trip a
# Parquet file. Catches a wheel for the wrong architecture or Python version, and a
# pruned shared library that turns out to be needed, before anything is deployed.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
arch="$(python3 -c "import json;print(json.load(open('$root/build/manifest.json'))['architecture'])")"
platform="linux/arm64"; [ "$arch" = "x86_64" ] && platform="linux/amd64"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
unzip -q "$root/build/compactor.zip" -d "$work"

docker run --rm --platform "$platform" --network none \
  -v "$work:/var/task:ro" --entrypoint python \
  public.ecr.aws/lambda/python:3.12 -c '
import platform, sys
import pyarrow
from webhook_platform import compaction
row = compaction.canonical_row({
    "event_id": "e1", "event_type": "t", "entity_type": "payment", "entity_id": "p1",
    "loan_application_id": "LA-1", "occurred_at": "2026-01-01T00:00:00.000Z", "version": 1,
    "status": "settled", "attributes_json": "{}", "ingested_at": "2026-01-01T00:00:01.000Z",
    "archive_key": "k", "correlation_id": "c",
})
assert compaction.from_parquet(compaction.to_parquet([row])) == [row]
print(f"compactor.zip loads: python {sys.version.split()[0]}, pyarrow {pyarrow.__version__}, {platform.machine()}")
'

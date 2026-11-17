"""The only place tools get AWS clients from. Emulator only.

Clients are built with a literal dummy key pair and an endpoint that has to be on
loopback. Explicit arguments take precedence over anything in the environment or in
~/.aws, so a real profile on the machine is never consulted.
"""

from __future__ import annotations

import json
import os
import re
from functools import cache
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

RUN_DIR = Path(os.environ.get("WEBHOOK_RUN_DIR", ".run"))
_LOOPBACK = re.compile(r"^http://(localhost|127\.0\.0\.1):\d{2,5}$")
_REGION = "us-east-1"


class SandboxError(RuntimeError):
    pass


def endpoint() -> str:
    url = os.environ.get("WEBHOOK_ENDPOINT_URL", "")
    if not _LOOPBACK.match(url):
        raise SandboxError(
            f"WEBHOOK_ENDPOINT_URL must be http://localhost:<port> or http://127.0.0.1:<port>, got {url!r}. "
            "These tools only run against the local emulator; use `make`, which sets it."
        )
    return url


@cache
def client(service: str) -> Any:
    return boto3.session.Session(
        aws_access_key_id="test",
        aws_secret_access_key="test",
        region_name=_REGION,
    ).client(
        service,
        endpoint_url=endpoint(),
        config=Config(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=60,
            max_pool_connections=32,
            s3={"addressing_style": "path"},
        ),
    )


@cache
def outputs() -> dict[str, Any]:
    path = RUN_DIR / "outputs.json"
    if not path.exists():
        raise SandboxError(f"{path} not found. Run `make deploy` first.")
    raw = json.loads(path.read_text())
    platform: dict[str, Any] = dict(raw["platform"]["value"])
    platform["alert_sink_queue_url"] = raw["alert_sink_queue_url"]["value"]
    return platform


def webhook_url(vendor: str) -> str:
    """Invoke URL on the emulator, which serves HTTP APIs under a path, not a hostname."""
    return f"{endpoint()}/execute-api/{outputs()['api_id']}/$default/webhooks/{vendor}"


def read_json(name: str) -> Any:
    return json.loads((RUN_DIR / name).read_text())


def write_json(name: str, value: Any) -> Path:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    path = RUN_DIR / name
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    return path

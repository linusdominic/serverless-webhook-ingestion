"""Environment configuration. Names of things only; secret values are fetched at runtime."""

from __future__ import annotations

import os


def env(name: str) -> str:
    try:
        return os.environ[name]
    except KeyError:
        raise RuntimeError(f"missing required environment variable {name}") from None


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw in (None, "") else int(raw)

"""CloudWatch metrics in embedded metric format (EMF).

Metrics are written as a log line that CloudWatch Logs turns into metrics
asynchronously. No PutMetricData call sits on the request path, and a metrics outage
cannot fail a webhook.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict

_NAMESPACE = os.environ.get("METRICS_NAMESPACE", "WebhookIngestion")
_FUNCTION = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "local")


class Metrics:
    """Collects counts during one invocation and flushes one EMF line per dimension set."""

    def __init__(self) -> None:
        self._counts: dict[tuple[tuple[str, str], ...], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._units: dict[str, str] = {}

    def add(self, name: str, value: float = 1, unit: str = "Count", **dimensions: str) -> None:
        key = tuple(sorted(dimensions.items()))
        self._counts[key][name] += value
        self._units[name] = unit

    def flush(self) -> list[str]:
        lines: list[str] = []
        now_ms = int(time.time() * 1000)
        for dims, values in self._counts.items():
            dimensions = {"Function": _FUNCTION, **dict(dims)}
            doc: dict[str, object] = {
                "_aws": {
                    "Timestamp": now_ms,
                    "CloudWatchMetrics": [
                        {
                            "Namespace": _NAMESPACE,
                            "Dimensions": [sorted(dimensions)],
                            "Metrics": [{"Name": n, "Unit": self._units[n]} for n in sorted(values)],
                        }
                    ],
                },
                **dimensions,
                **values,
            }
            lines.append(json.dumps(doc, separators=(",", ":")))
        self._counts.clear()
        for line in lines:
            sys.stdout.write(line + "\n")
        return lines

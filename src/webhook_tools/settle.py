"""Wait for the system to finish what the load generator started.

1. The ingest queue is empty and the poison messages have exhausted their receives
   and reached the DLQ.
2. A curation pipeline execution that started after that point has completed. It is
   started by EventBridge Scheduler, not by this script.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from . import sandbox
from .world import Truth, World


def wait_for_drain(world: World, expected_dlq: int, timeout: float) -> float:
    started = time.monotonic()
    stable, last_report = 0, 0.0
    while True:
        visible, in_flight = world.queue_counts(world.out["queue_url"])
        dead = sum(world.queue_counts(world.out["dlq_url"]))
        elapsed = time.monotonic() - started
        stable = stable + 1 if (visible, in_flight) == (0, 0) and dead >= expected_dlq else 0
        if stable >= 3:
            return elapsed
        if elapsed - last_report >= 10:
            print(f"settle: {elapsed:5.0f}s  queue {visible} visible, {in_flight} in flight; dlq {dead}/{expected_dlq}")
            last_report = elapsed
        if elapsed > timeout:
            raise SystemExit(f"settle: queue did not drain within {timeout:.0f}s")
        time.sleep(1)


def wait_for_pipeline(world: World, after: datetime, timeout: float) -> dict[str, object]:
    started = time.monotonic()
    while True:
        finished = [e for e in world.executions() if e["startDate"] > after and e["status"] != "RUNNING"]
        if finished:
            execution = finished[-1]  # the first one that started after the drain
            return {
                "execution_arn": execution["executionArn"],
                "status": execution["status"],
                "started": execution["startDate"].isoformat(),
                "seconds": round((execution["stopDate"] - execution["startDate"]).total_seconds(), 2),
                "states": world.execution_states(execution["executionArn"]),
                "waited_seconds": round(time.monotonic() - started, 1),
            }
        if time.monotonic() - started > timeout:
            raise SystemExit(f"settle: no scheduled pipeline execution completed within {timeout:.0f}s")
        time.sleep(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replay", action="store_true", help="settling after a replay rather than after the load generator"
    )
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()

    world, truth = World(), Truth.load()
    replays = sandbox.read_json("replays.json")["count"]
    expected_dlq = len(truth.poison_keys) * (1 + replays)

    drain_seconds = wait_for_drain(world, expected_dlq, args.timeout)
    drained_at = datetime.now(tz=timezone.utc)
    print(f"settle: queue drained after {drain_seconds:.1f}s; {expected_dlq} message(s) in the DLQ")

    pipeline = wait_for_pipeline(world, drained_at, 300)
    print(
        f"settle: scheduled pipeline execution {pipeline['status']} in {pipeline['seconds']}s "
        f"(waited {pipeline['waited_seconds']}s for the schedule): {' > '.join(pipeline['states'])}"  # type: ignore[arg-type]
    )

    record = {"drained_at": drained_at.isoformat(), "drain_seconds": round(drain_seconds, 1), "pipeline": pipeline}
    sandbox.write_json("settle-replay.json" if args.replay else "settle-demo.json", record)
    sandbox.write_json("settle.json", record)
    return 0 if pipeline["status"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

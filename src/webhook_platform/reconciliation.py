"""Reconciliation arithmetic. Pure.

Reconciliation compares two counts of the same set: events ingested in a window
[start, end), per vendor. One count comes from the processor's ledger in DynamoDB,
the other from the compacted Parquet table through Athena.

The window is recent and bounded (the last ``lookback`` hours, ending ``margin``
seconds before compaction started) so that the cost of a run does not grow with
history. The ledger is read through an index whose partition key is
"<vendor>#<ingest date>", so a run touches at most a few days' worth of index
entries however large the table is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime  # exclusive

    @staticmethod
    def _iso(ts: datetime) -> str:
        return ts.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @property
    def start_iso(self) -> str:
        return self._iso(self.start)

    @property
    def end_iso(self) -> str:
        return self._iso(self.end)

    @property
    def last_included_iso(self) -> str:
        """Timestamps are stored to the millisecond, so [start, end) is the closed
        range [start, end - 1 ms]. DynamoDB's BETWEEN is closed at both ends."""
        return self._iso(self.end - timedelta(milliseconds=1))

    @staticmethod
    def _sql(ts: datetime) -> str:
        # Formatted from a datetime, so the text is safe to place in a query.
        utc = ts.astimezone(timezone.utc)
        return utc.strftime("%Y-%m-%d %H:%M:%S.") + f"{utc.microsecond // 1000:03d}"

    @property
    def start_sql(self) -> str:
        return self._sql(self.start)

    @property
    def end_sql(self) -> str:
        return self._sql(self.end)

    def ingest_days(self) -> list[str]:
        """Every UTC date the window touches, oldest first."""
        first = self.start.astimezone(timezone.utc).date()
        last = (self.end - timedelta(milliseconds=1)).astimezone(timezone.utc).date()
        return [(first + timedelta(days=n)).isoformat() for n in range((last - first).days + 1)]


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no offset")
    return parsed.astimezone(timezone.utc)


def window(compaction_started_at: str, *, margin_seconds: int, lookback_hours: int) -> Window:
    if lookback_hours < 1:
        raise ValueError("lookback_hours must be at least 1")
    end = parse_timestamp(compaction_started_at) - timedelta(seconds=margin_seconds)
    # Whole milliseconds, like the stored timestamps.
    end = end.replace(microsecond=end.microsecond // 1000 * 1000)
    return Window(start=end - timedelta(hours=lookback_hours), end=end)


def ingest_day_key(vendor: str, ingested_at_iso: str) -> str:
    """Partition key of the ledger's by-ingest-day index."""
    return f"{vendor}#{ingested_at_iso[:10]}"

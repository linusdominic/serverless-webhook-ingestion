-- An event cannot have happened more than a day after we ingested it. Catches
-- unit mistakes (seconds read as milliseconds) and vendor clock faults.
SELECT count(*) AS violations
FROM events
WHERE occurred_at > ingested_at + INTERVAL '1' DAY

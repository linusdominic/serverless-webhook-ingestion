-- Rows live in the partition of their own event date. A mismatch means the processor
-- and the compactor disagree about partitioning and deduplication is unsound.
SELECT count(*) AS violations
FROM events
WHERE dt <> substr(cast(occurred_at AS varchar), 1, 10)

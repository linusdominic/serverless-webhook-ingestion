-- One row per (vendor, event_id) in the compacted table. A duplicate means the
-- compactor's merge is wrong or two partitions hold the same event.
SELECT count(*) AS violations
FROM (
    SELECT vendor, event_id
    FROM events
    GROUP BY vendor, event_id
    HAVING count(*) > 1
) AS duplicated

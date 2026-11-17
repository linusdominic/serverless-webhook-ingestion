-- Fields every consumer relies on are never null or empty.
SELECT count(*) AS violations
FROM events
WHERE event_id IS NULL OR event_id = ''
   OR entity_id IS NULL OR entity_id = ''
   OR loan_application_id IS NULL OR loan_application_id = ''
   OR status IS NULL OR status = ''
   OR occurred_at IS NULL
   OR ingested_at IS NULL

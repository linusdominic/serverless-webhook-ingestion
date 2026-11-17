-- Status is one of the values the normaliser can produce for that vendor. A new
-- value here means a mapping changed without the consumers being told.
SELECT count(*) AS violations
FROM events
WHERE NOT (
       (vendor = 'meridianpay' AND status IN ('initiated', 'settled', 'failed', 'returned'))
    OR (vendor = 'callwright' AND status IN ('ringing', 'in_progress', 'completed'))
    OR (vendor = 'veritybureau' AND status IN ('pending', 'in_review', 'complete'))
)

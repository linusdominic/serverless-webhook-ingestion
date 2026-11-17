-- Each vendor produces exactly one entity type. Anything else is a normaliser bug.
SELECT count(*) AS violations
FROM events
WHERE NOT (
       (vendor = 'meridianpay' AND entity_type = 'payment')
    OR (vendor = 'callwright' AND entity_type = 'call')
    OR (vendor = 'veritybureau' AND entity_type = 'verification')
)

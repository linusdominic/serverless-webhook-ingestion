-- The curated zone carries tokens, never the raw fields. These are the vendor field
-- names for phone numbers, SSN fragments, names and dates of birth.
SELECT count(*) AS violations
FROM events
WHERE attributes_json LIKE '%"phone":%'
   OR attributes_json LIKE '%"from":%'
   OR attributes_json LIKE '%"to":%'
   OR attributes_json LIKE '%"ssn_last4":%'
   OR attributes_json LIKE '%"full_name":%'
   OR attributes_json LIKE '%"date_of_birth":%'

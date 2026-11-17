# ADR 0006: Secrets outside Terraform state; PII confined to the raw bucket

Status: accepted, 2026-11-17

## Secrets

**Terraform creates secret containers and never a secret value.** Vendor signing keys
and the PII tokenisation key are written to Secrets Manager out of band (by
`make seed` on the emulator, by whoever onboards the vendor on AWS). Nothing secret
is in a variable, a `.tfvars` file or state.

**Functions read secrets at runtime, by name, and cache them in memory.** No secret is
in an environment variable, where it would be visible in the console, in
`GetFunctionConfiguration` and in state. The verifier checks this: it reads every
secret value and searches function configuration, logs and queues for them.

**A signing secret is a list of keys, current first.** Rotation is: add the new key,
wait for the vendor to switch, remove the old key. Both verify in between. The
receiver caches for five minutes; when a signature fails it re-reads the secret once,
at most every 30 seconds per vendor, so a rotation is picked up on the first request
after it without letting a flood of forged requests become a flood of Secrets
Manager calls.

## PII

Vendor payloads contain phone numbers, an SSN fragment, names and dates of birth.

**The raw bucket is the only place they exist in the clear.** It has its own KMS key.
The receiver can encrypt with that key and not decrypt; the processor can decrypt
and not write; the bucket policy denies reads to every principal not on a short
list. Queues, the DLQ and quarantine hold pointers, never payloads. Logs carry ids
and reasons, never bodies or headers, and exceptions are logged without tracebacks
because the processor's locals are payloads.

**Everything downstream gets tokens.** Phone numbers and the SSN fragment are replaced
by `HMAC-SHA256(pepper, kind + ":" + value)`, truncated to 128 bits. Names and dates
of birth are dropped, since nothing downstream needs them.

A keyed token rather than a hash, because the inputs are tiny: four digits have
10,000 possible values and a phone number about ten billion, so an unkeyed SHA-256 is
reversed by enumeration in seconds. With the key, tokens are still stable (equal
inputs give equal tokens, so joins and counts work) and only someone who can read the
pepper can enumerate. The `kind` prefix keeps a phone extension and an SSN fragment
that happen to be equal from producing equal tokens.

## Consequences

- Tokens are pseudonymous, not anonymous. Anyone with the pepper can test a guess.
  The pepper needs the same care as the raw bucket.
- Rotating the pepper changes every token and breaks joins with existing curated
  data. It is done deliberately, with a forced replay to re-tokenise, never on a
  timer; the Terraform says so where a scanner asks for automatic rotation.
- Vendor secrets are rotated by the vendor, so there is no rotation Lambda.
- On the emulator none of the access rules above is enforced: IAM and bucket policies
  are stored, not evaluated. What is verified is the behaviour of the code (no PII or
  secret values outside the raw bucket) and that the policies are syntactically
  accepted. Whether a given role is actually denied has not been tested.

"""Demo and operations tooling: load generator, verifier, replay, redrive.

Nothing in this package imports webhook_platform. The generator signs requests and
the verifier computes expected state and PII tokens with their own code, so a
mistake in the platform cannot hide behind the same mistake in its test harness.
tests/unit/test_tools_independent.py enforces the import rule.
"""

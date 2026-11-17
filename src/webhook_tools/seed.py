"""Write signing keys and the PII pepper into the emulator's Secrets Manager.

Terraform creates the secrets empty. On a real account the values come from the
vendor's onboarding portal and a one-off key generation (docs/runbook.md); here they
are generated. Existing values are left alone, so re-running is a no-op.

Each vendor gets two keys, current and previous, as it would mid-rotation.
"""

from __future__ import annotations

import json
import secrets

from botocore.exceptions import ClientError

from . import sandbox


def _has_value(secret_id: str) -> bool:
    try:
        sandbox.client("secretsmanager").get_secret_value(SecretId=secret_id)
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


def main() -> int:
    out = sandbox.outputs()
    sm = sandbox.client("secretsmanager")
    written = 0
    for vendor, name in sorted(out["signing_secret_names"].items()):
        if _has_value(name):
            continue
        value = {
            "keys": [
                {"id": f"{vendor}-2026-11", "secret": secrets.token_hex(32)},
                {"id": f"{vendor}-2026-05", "secret": secrets.token_hex(32)},
            ]
        }
        sm.put_secret_value(SecretId=name, SecretString=json.dumps(value))
        written += 1
    pepper = out["pii_pepper_secret_name"]
    if not _has_value(pepper):
        sm.put_secret_value(SecretId=pepper, SecretString=json.dumps({"pepper": secrets.token_hex(32)}))
        written += 1
    print(f"seed: {written} secret value(s) written, {len(out['signing_secret_names']) + 1 - written} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

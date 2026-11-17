"""Create-only S3 writes."""

from __future__ import annotations

import time
from typing import Any

from botocore.exceptions import ClientError

_ALREADY_EXISTS = {"PreconditionFailed", "412"}
_CONCURRENT_WRITE = {"ConditionalRequestConflict", "409"}


def put_if_absent(s3: Any, **put_object_arguments: Any) -> bool:
    """PutObject with If-None-Match: *. Returns True if the object was created,
    False if one already existed at the key (it is left untouched).

    On a versioned bucket a plain put of identical bytes still adds a version. For
    writes that are repeated on every redelivery or replay, create-only keeps "do it
    again" from meaning "store it again".

    S3 answers 409 when two conditional writes to one key overlap; the loser retries
    and then sees the winner's object.
    """
    for attempt in range(3):
        try:
            s3.put_object(IfNoneMatch="*", **put_object_arguments)
            return True
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code"))
            if code in _ALREADY_EXISTS:
                return False
            if code in _CONCURRENT_WRITE and attempt < 2:
                time.sleep(0.05 * (attempt + 1))
                continue
            raise
    raise AssertionError("unreachable")  # pragma: no cover

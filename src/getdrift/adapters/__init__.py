"""Adapters that turn other harnesses' output into a schema-valid results.json."""

import re

#: The schema's timestamp rule: ISO 8601 with an explicit UTC offset.
OFFSET_AWARE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$"
)


def is_offset_aware(value: object) -> bool:
    """Whether a timestamp satisfies the schema before it is copied onto every case.

    A harness timestamp is one field, but adapters fan it out across every case — so an
    unchecked bad value becomes N identical schema errors with nothing pointing at the
    single field that caused them.
    """
    return isinstance(value, str) and bool(OFFSET_AWARE.match(value))

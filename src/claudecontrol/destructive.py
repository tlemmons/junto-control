from __future__ import annotations

import re

# Mirror of the server-side destructive-gate regex from
# claudeControl:message_api v1.0.0 §Sending a message.
#
# The server checks:
#   \b(DELETE|DROP|TRUNCATE|deploy|production|prod\b)\b|git\s+push\s+(--force|-f)\b
# case-insensitive. We mirror it client-side ONLY for preview purposes
# (warning the human their send will require receiver-side human review).
# The server is the source of truth — never rely on this for security.
DESTRUCTIVE_PATTERN = re.compile(
    r"\b(DELETE|DROP|TRUNCATE|deploy|production|prod\b)\b"
    r"|git\s+push\s+(--force|-f)\b",
    re.IGNORECASE,
)


def matches_destructive(text: str) -> list[str]:
    """Return the matched substrings (lowercased) — empty list if clean."""
    if not text:
        return []
    return [m.group(0) for m in DESTRUCTIVE_PATTERN.finditer(text)]

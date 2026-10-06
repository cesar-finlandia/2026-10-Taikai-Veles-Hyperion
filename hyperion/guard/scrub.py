"""Output scrubber (DP-GUARDRAILS §5.7)."""
from __future__ import annotations
import re
from typing import Sequence

SECRET_PATTERNS = (r"sk-[A-Za-z0-9_\-]{12,}", r"Bearer\s+[A-Za-z0-9._\-]{16,}")

_COMPILED = tuple(re.compile(p) for p in SECRET_PATTERNS)


def scrub_output(text: str, secrets: Sequence[str]) -> str:
    """Replace every secret of length >= 6 and every match of SECRET_PATTERNS with '[redacted]'."""
    out = text
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        if len(secret) >= 6:
            out = out.replace(secret, "[redacted]")
    for rx in _COMPILED:
        out = rx.sub("[redacted]", out)
    return out


class StreamScrubber:
    """Scrub a stream of increments without missing a secret split across chunks."""

    def __init__(self, secrets: Sequence[str], *, hold: int = 40) -> None:
        self._secrets = list(secrets)
        self._hold = hold
        self._held = ""

    def feed(self, chunk: str) -> str:
        """Return the part of (held + chunk) that is safe to emit; keep the last `hold` characters back."""
        buf = scrub_output(self._held + chunk, self._secrets)
        if len(buf) > self._hold:
            emit = buf[:-self._hold]
            self._held = buf[-self._hold:]
            return emit
        self._held = buf
        return ""

    def flush(self) -> str:
        """Return the scrubbed remainder."""
        out = scrub_output(self._held, self._secrets)
        self._held = ""
        return out

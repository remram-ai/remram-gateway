from __future__ import annotations

import re


_INLINE_SECRET_RE = re.compile(
    r"\b([A-Za-z0-9_]*_(?:KEY|TOKEN|SECRET)|PASSWORD)=([^\s\"',}]+|\"[^\"]*\"|'[^']*')"
)
_JSON_SECRET_RE = re.compile(
    r'("?[A-Za-z0-9_]*_(?:KEY|TOKEN|SECRET)"?\s*:\s*)("([^"\\]|\\.)*"|\'([^\'\\]|\\.)*\'|[^,\r\n}]+)',
    re.MULTILINE,
)


def redact_text(value: str) -> str:
    redacted = _INLINE_SECRET_RE.sub(r"\1=REDACTED", value)
    redacted = _JSON_SECRET_RE.sub(r"\1\"REDACTED\"", redacted)
    return redacted

"""Request-body parsing and text sanitising for the turn pipeline (DP-AGENT-CORE §5.1)."""
from __future__ import annotations

import json
import unicodedata

_USER_KEYS = ("user_id", "userId", "user", "id")
_TEXT_KEYS = ("text", "message", "prompt", "query", "content")


def sanitize_text(text: str, *, limit: int | None = None) -> str:
    """NFC-normalise, drop NUL / BOM / C0 control characters except \\n \\t, turn \\r\\n and \\r into \\n; when limit is given
    truncate to its first `limit` characters. Never raises."""
    try:
        if not isinstance(text, str):
            try:
                text = str(text)
            except Exception:
                return ""
        s = unicodedata.normalize("NFC", text)
        s = s.replace("\r\n", "\n").replace("\r", "\n")
        out: list[str] = []
        for c in s:
            o = ord(c)
            if o == 0xFEFF or o == 0x7F:
                continue
            if o < 32 and c not in ("\n", "\t"):
                continue
            out.append(c)
        s = "".join(out)
        if limit is not None:
            s = s[:limit]
        return s
    except Exception:
        return ""


def parse_chat_body(raw: bytes | str | None) -> tuple[str, str]:
    """(user_id, text) from a request body of unknown quality. See §5.1. Never raises."""
    try:
        if raw is None:
            return ("anonymous", "")
        if isinstance(raw, bytes):
            try:
                s = raw.decode("utf-8", errors="replace")
            except Exception:
                return ("anonymous", "")
        elif isinstance(raw, str):
            s = raw
        else:
            return ("anonymous", "")
        if not s:
            return ("anonymous", "")
        try:
            data = json.loads(s)
        except Exception:
            return ("anonymous", "")
        if isinstance(data, str):
            return ("anonymous", data)
        if not isinstance(data, dict):
            return ("anonymous", "")
        user_id = "anonymous"
        for key in _USER_KEYS:
            if key not in data:
                continue
            value = data[key]
            if value is None:
                continue
            try:
                cand = str(value).strip()
            except Exception:
                continue
            if not cand:
                continue
            try:
                cand = sanitize_text(cand)[:128]
            except Exception:
                continue
            if cand:
                user_id = cand
                break
        text = ""
        for key in _TEXT_KEYS:
            if key not in data:
                continue
            value = data[key]
            if value is None:
                text = ""
            elif isinstance(value, str):
                text = value
            elif isinstance(value, bool):
                text = str(value)
            elif isinstance(value, (int, float)):
                text = str(value)
            elif isinstance(value, (list, dict)):
                try:
                    text = json.dumps(value, ensure_ascii=False)
                except Exception:
                    text = ""
            else:
                try:
                    text = str(value)
                except Exception:
                    text = ""
            break
        return (user_id, text)
    except Exception:
        return ("anonymous", "")

"""Typed access to the IDE backend helpers read_file and validate_file."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from time import monotonic
from typing import Any, Literal

import httpx

from hyperion.config import Settings
from hyperion.issues import Issue

ReadStatus = Literal["ok", "not_found", "ambiguous", "unreachable", "error"]


@dataclass(frozen=True)
class FileReadResult:
    status: ReadStatus
    path: str | None  # the actual path the IDE reports (set when status == 'ok')
    content: str | None
    matches: tuple[str, ...]  # filled when status == 'ambiguous'
    message: str  # a sentence safe to show the user

    @property
    def ok(self) -> bool:
        """True when the file was read (status == 'ok')."""
        return self.status == "ok"


@dataclass(frozen=True)
class ValidationResult:
    status: ReadStatus
    valid: bool | None
    type: str | None  # e.g. 'native' | 'device' as reported by the IDE
    errors: tuple[Issue, ...]
    warnings: tuple[Issue, ...]
    raw: dict[str, Any]
    message: str

    @property
    def ok(self) -> bool:
        """status == 'ok' (the call worked; says nothing about validity)."""
        return self.status == "ok"


@dataclass(frozen=True)
class LandingResult:
    landed: bool
    result: FileReadResult  # the last read performed
    waited_s: float


_MESSAGE_KEYS = ("message", "msg", "error", "text", "description", "reason")
_LINE_KEYS = ("line", "lineNumber", "row")
_COL_KEYS = ("col", "column", "columnNumber", "character")
_PATH_KEYS = ("path", "instancePath", "field", "property", "key")


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _to_issue(item: Any, severity: Literal["error", "warning"]) -> Issue:
    if isinstance(item, str):
        return Issue(severity=severity, code="remote", message=item, source="remote")
    if isinstance(item, dict):
        message: str | None = None
        for key in _MESSAGE_KEYS:
            v = item.get(key)
            if isinstance(v, str) and v.strip():
                message = v
                break
        if message is None:
            message = json.dumps(item)[:200]
        line: int | None = None
        for key in _LINE_KEYS:
            line = _as_int(item.get(key))
            if line is not None:
                break
        col: int | None = None
        for key in _COL_KEYS:
            col = _as_int(item.get(key))
            if col is not None:
                break
        ipath: str | None = None
        for key in _PATH_KEYS:
            v = item.get(key)
            if isinstance(v, str) and v:
                ipath = v[1:] if v.startswith("/") else v
                ipath = ipath.replace("/", ".")
                break
        return Issue(
            severity=severity,
            code="remote",
            message=message,
            path=ipath,
            line=line,
            col=col,
            source="remote",
        )
    return Issue(severity=severity, code="remote", message=str(item)[:200], source="remote")


def normalize_report(raw: dict[str, Any]) -> tuple[bool | None, str | None, tuple[Issue, ...], tuple[Issue, ...]]:
    """(valid, type, errors, warnings) from an IDE validation report of unknown item shape. See §5.2."""
    rtype = raw.get("type")
    vtype: str | None = rtype if isinstance(rtype, str) else None
    errors_raw = raw.get("errors") or []
    warnings_raw = raw.get("warnings") or []
    if not isinstance(errors_raw, list):
        errors_raw = []
    if not isinstance(warnings_raw, list):
        warnings_raw = []
    errors = tuple(_to_issue(i, "error") for i in errors_raw)
    warnings = tuple(_to_issue(i, "warning") for i in warnings_raw)
    raw_valid = raw.get("valid")
    valid: bool | None = raw_valid if isinstance(raw_valid, bool) else len(errors) == 0
    return (valid, vtype, errors, warnings)


def _norm(s: str | None) -> str:
    return (s or "").replace("\r\n", "\n").rstrip()


class IdeGateway:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            # NOTE: plan §5.3 spells `base_url=None`; the installed httpx
            # rejects an explicit None, so it is omitted (all requests use
            # absolute URLs, hence behaviour is identical).
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=3.0),
                transport=self._transport,
            )
        return self._client

    def _read_url(self) -> str:
        return f"{self._settings.ide_backend_url}/agent/file"

    def _validate_url(self) -> str:
        return f"{self._settings.ide_backend_url}/agent/validation/file"

    def _parse_body(self, response: httpx.Response) -> dict[str, Any] | None:
        try:
            body = response.json()
        except Exception:
            return None
        return body if isinstance(body, dict) else None

    async def read(self, path: str) -> FileReadResult:
        """GET {ide_backend_url}/agent/file?path=<path>."""
        url = self._read_url()
        try:
            response = await self._client_or_create().get(url, params={"path": path})
        except (httpx.TransportError, TimeoutError) as e:
            _ = e
            return FileReadResult(
                status="unreachable",
                path=None,
                content=None,
                matches=(),
                message=f"I cannot reach the IDE backend at {url}.",
            )
        except Exception as e:
            return FileReadResult(
                status="error",
                path=None,
                content=None,
                matches=(),
                message=f"The IDE backend request failed: {e}.",
            )
        if response.status_code == 200:
            body = self._parse_body(response)
            if body is None:
                return FileReadResult(
                    status="error",
                    path=None,
                    content=None,
                    matches=(),
                    message=f"The IDE backend answered HTTP {response.status_code}.",
                )
            actual = body.get("path")
            content = body.get("content")
            return FileReadResult(
                status="ok",
                path=actual if isinstance(actual, str) else path,
                content=content if isinstance(content, str) else "",
                matches=(),
                message="",
            )
        if response.status_code == 404:
            return FileReadResult(
                status="not_found",
                path=None,
                content=None,
                matches=(),
                message=f"`{path}` is not in the workspace.",
            )
        if response.status_code == 409:
            body = self._parse_body(response)
            matches: tuple[str, ...] = ()
            if body is not None:
                raw_matches = body.get("matches") or []
                if isinstance(raw_matches, list):
                    matches = tuple(m for m in raw_matches if isinstance(m, str))
            return FileReadResult(
                status="ambiguous",
                path=None,
                content=None,
                matches=matches,
                message=f"Several files are named `{path}` ({', '.join(matches)}). Tell me the full path.",
            )
        body = self._parse_body(response)
        message: str
        if body is not None and isinstance(body.get("error"), str):
            message = body["error"]
        else:
            message = f"The IDE backend answered HTTP {response.status_code}."
        return FileReadResult(status="error", path=None, content=None, matches=(), message=message)

    async def exists(self, path: str) -> bool | None:
        """True when read() is 'ok' or 'ambiguous' (something with that name exists); False when 'not_found';
        None when 'unreachable' or 'error' (existence unknown)."""
        result = await self.read(path)
        if result.status in ("ok", "ambiguous"):
            return True
        if result.status == "not_found":
            return False
        return None

    async def validate(self, path: str) -> ValidationResult:
        """GET {ide_backend_url}/agent/validation/file?path=<path>."""
        url = self._validate_url()
        try:
            response = await self._client_or_create().get(url, params={"path": path})
        except (httpx.TransportError, TimeoutError) as e:
            _ = e
            return ValidationResult(
                status="unreachable",
                valid=None,
                type=None,
                errors=(),
                warnings=(),
                raw={},
                message=f"I cannot reach the IDE backend at {url}.",
            )
        except Exception as e:
            return ValidationResult(
                status="error",
                valid=None,
                type=None,
                errors=(),
                warnings=(),
                raw={},
                message=f"The IDE backend request failed: {e}.",
            )
        if response.status_code == 200:
            body = self._parse_body(response)
            if body is None:
                return ValidationResult(
                    status="error",
                    valid=None,
                    type=None,
                    errors=(),
                    warnings=(),
                    raw={},
                    message=f"The IDE backend answered HTTP {response.status_code}.",
                )
            valid, vtype, errors, warnings = normalize_report(body)
            if valid:
                message = f"`{path}` is valid."
            else:
                message = f"`{path}` has {len(errors)} problem(s)."
            return ValidationResult(
                status="ok",
                valid=valid,
                type=vtype,
                errors=errors,
                warnings=warnings,
                raw=body,
                message=message,
            )
        if response.status_code == 404:
            return ValidationResult(
                status="not_found",
                valid=None,
                type=None,
                errors=(),
                warnings=(),
                raw={},
                message=f"`{path}` is not in the workspace.",
            )
        if response.status_code == 409:
            body = self._parse_body(response)
            matches: list[str] = []
            if body is not None:
                raw_matches = body.get("matches") or []
                if isinstance(raw_matches, list):
                    matches = [m for m in raw_matches if isinstance(m, str)]
            return ValidationResult(
                status="ambiguous",
                valid=None,
                type=None,
                errors=(),
                warnings=(),
                raw=body or {},
                message=f"Several files are named `{path}` ({', '.join(matches)}). Tell me the full path.",
            )
        body = self._parse_body(response)
        if body is not None and isinstance(body.get("error"), str):
            message = body["error"]
        else:
            message = f"The IDE backend answered HTTP {response.status_code}."
        return ValidationResult(
            status="error",
            valid=None,
            type=None,
            errors=(),
            warnings=(),
            raw=body or {},
            message=message,
        )

    async def await_landing(
        self,
        path: str,
        *,
        expect_content: str | None = None,
        expect_absent: bool = False,
        timeout_s: float | None = None,
    ) -> LandingResult:
        """Poll read() until the IDE frontend has applied an action. See §5.3."""
        timeout = timeout_s if timeout_s is not None else self._settings.landing_timeout_s
        interval = self._settings.landing_interval_s
        t0 = monotonic()
        unreachable_streak = 0
        last: FileReadResult = FileReadResult(status="error", path=None, content=None, matches=(), message="")
        while True:
            last = await self.read(path)
            if last.status == "unreachable":
                unreachable_streak += 1
                if unreachable_streak >= 2:
                    return LandingResult(landed=False, result=last, waited_s=monotonic() - t0)
            else:
                unreachable_streak = 0
                if expect_absent:
                    if last.status == "not_found":
                        return LandingResult(landed=True, result=last, waited_s=monotonic() - t0)
                elif expect_content is not None:
                    if last.status == "ok" and _norm(last.content) == _norm(expect_content):
                        return LandingResult(landed=True, result=last, waited_s=monotonic() - t0)
                elif last.status == "ok":
                    return LandingResult(landed=True, result=last, waited_s=monotonic() - t0)
            if monotonic() - t0 >= timeout:
                return LandingResult(landed=False, result=last, waited_s=monotonic() - t0)
            await asyncio.sleep(interval)
            interval = min(1.0, interval * 1.3)

    async def probe(self) -> dict[str, Any]:
        """{'reachable': bool, 'url': str, 'status': int | None}; never raises."""
        url = self._settings.ide_backend_url
        try:
            response = await self._client_or_create().get(url)
            return {"reachable": True, "url": url, "status": response.status_code}
        except Exception:
            return {"reachable": False, "url": url, "status": None}

    async def aclose(self) -> None:
        """Close the underlying HTTP client, if one was created."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

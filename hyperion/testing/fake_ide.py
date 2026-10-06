"""A fake IDE backend + workspace, for tests and offline evaluation. Speaks the documented backend contract."""
from __future__ import annotations

import asyncio
from typing import Any, Callable

import httpx

from hyperion.ide.paths import basename, parent_dirs

Validator = Callable[[str, str], dict[str, Any]]  # (path, content) -> IDE-shaped report dict


def _default_validator(path: str, content: str) -> dict[str, Any]:
    _ = content
    return {"path": path, "type": "unknown", "valid": True, "errors": [], "warnings": []}


class FakeWorkspace:
    files: dict[str, str]
    folders: set[str]
    create_parents: bool

    def __init__(self, files: dict[str, str] | None = None, *, create_parents: bool = True) -> None:
        self.files = dict(files) if files else {}
        self.create_parents = create_parents
        self.folders = set()
        for p in self.files:
            self.folders.update(parent_dirs(p))

    def resolve(self, path: str) -> tuple[str, list[str]]:
        """('one', [actual]) | ('none', []) | ('many', [matches...]). Full path exact match wins; a bare name matches by basename."""
        if path in self.files:
            return ("one", [path])
        if "/" not in path:
            matches = sorted(p for p in self.files if basename(p) == path)
            if len(matches) == 0:
                return ("none", [])
            if len(matches) == 1:
                return ("one", matches)
            return ("many", matches)
        return ("none", [])

    def _ensure_parents(self, path: str) -> bool:
        parents = parent_dirs(path)
        if not self.create_parents:
            for folder in parents:
                if folder not in self.folders:
                    return False
        self.folders.update(parents)
        return True

    def apply(self, payload: dict[str, str]) -> bool:
        """Apply an action payload like the IDE frontend would; return False when it could not be applied."""
        action = payload.get("action")
        path = payload.get("path", "")
        if not isinstance(action, str) or not isinstance(path, str) or not path:
            return False
        if action == "create_folder":
            self.folders.add(path)
            self.folders.update(parent_dirs(path))
            return True
        if action == "create_file":
            if not self._ensure_parents(path):
                return False
            self.files[path] = payload.get("content", "")
            return True
        if action == "edit_file":
            kind, matches = self.resolve(path)
            if kind == "one":
                self.files[matches[0]] = payload.get("content", "")
                return True
            if kind == "many":
                self.files[sorted(matches)[0]] = payload.get("content", "")
                return True
            if not self._ensure_parents(path):
                return False
            self.files[path] = payload.get("content", "")
            return True
        if action == "delete_file":
            kind, matches = self.resolve(path)
            if kind == "none":
                return False
            del self.files[sorted(matches)[0]]
            return True
        if action == "delete_folder":
            prefix = path + "/"
            known = path in self.folders or any(f.startswith(prefix) for f in self.files) or any(
                d.startswith(prefix) for d in self.folders
            )
            if not known:
                return False
            self.folders.discard(path)
            self.folders = {d for d in self.folders if not d.startswith(prefix)}
            for f in [f for f in self.files if f.startswith(prefix)]:
                del self.files[f]
            return True
        return False

    def snapshot(self) -> dict[str, str]:
        """Return a copy of the current files."""
        return dict(self.files)


class FakeIdeBackend:
    workspace: FakeWorkspace
    calls: list[tuple[str, str]]  # (endpoint, path) in order
    down: bool

    def __init__(
        self,
        workspace: FakeWorkspace | None = None,
        *,
        validator: Validator | None = None,
        latency_s: float = 0.0,
    ) -> None:
        self.workspace = workspace if workspace is not None else FakeWorkspace()
        self.validator: Validator = validator if validator is not None else _default_validator
        self.latency_s = latency_s
        self.calls = []
        self.down = False

    def transport(self) -> httpx.MockTransport:
        """Async handler for GET /api/agent/file and GET /api/agent/validation/file under any host."""

        async def handler(request: httpx.Request) -> httpx.Response:
            endpoint = request.url.path
            query_path = request.url.params.get("path", "")
            self.calls.append((endpoint, query_path))
            if self.down:
                raise httpx.ConnectError("fake IDE backend is down")
            if self.latency_s:
                await asyncio.sleep(self.latency_s)
            if endpoint.endswith("/agent/validation/file"):
                kind, matches = self.workspace.resolve(query_path)
                if kind == "none":
                    return httpx.Response(404, json={"error": "not found"})
                if kind == "many":
                    return httpx.Response(409, json={"error": "ambiguous", "matches": matches})
                actual = matches[0]
                return httpx.Response(200, json=self.validator(actual, self.workspace.files[actual]))
            if endpoint.endswith("/agent/file"):
                kind, matches = self.workspace.resolve(query_path)
                if kind == "none":
                    return httpx.Response(404, json={"error": "not found"})
                if kind == "many":
                    return httpx.Response(409, json={"error": "ambiguous", "matches": matches})
                actual = matches[0]
                return httpx.Response(200, json={"path": actual, "content": self.workspace.files[actual]})
            return httpx.Response(404, json={"error": "not found"})

        return httpx.MockTransport(handler)

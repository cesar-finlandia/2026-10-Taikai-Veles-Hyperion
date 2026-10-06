"""Per-user serialisation locks (DP-AGENT-CORE §3)."""
from __future__ import annotations

import asyncio
import contextlib
from collections import OrderedDict
from typing import AsyncIterator


class UserLocks:
    """One asyncio.Lock per user_id, so one user's turns are serialised and different users run concurrently."""

    def __init__(self, max_users: int = 5000) -> None:
        self._max_users = max_users
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()

    def _evict(self, keep: str) -> None:
        while len(self._locks) > self._max_users:
            dropped = False
            for key, lock in list(self._locks.items()):
                if key == keep:
                    continue
                if not lock.locked():
                    del self._locks[key]
                    dropped = True
                    if len(self._locks) <= self._max_users:
                        break
            if not dropped:
                break

    @contextlib.asynccontextmanager
    async def hold(self, user_id: str, *, timeout: float | None = None) -> AsyncIterator[bool]:
        """Acquire the user's lock; yield True when held (released on exit), False when `timeout` seconds passed
        without acquiring (nothing to release). When the table exceeds max_users, unlocked entries are dropped oldest first."""
        lock = self._locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[user_id] = lock
            self._locks.move_to_end(user_id)
            self._evict(user_id)
        else:
            self._locks.move_to_end(user_id)
        if timeout is None:
            await lock.acquire()
            try:
                yield True
            finally:
                lock.release()
        else:
            try:
                await asyncio.wait_for(lock.acquire(), timeout=timeout)
            except (asyncio.TimeoutError, TimeoutError):
                yield False
            else:
                try:
                    yield True
                finally:
                    lock.release()

    def size(self) -> int:
        return len(self._locks)

"""A per-user fixed window for expensive routes within one server process."""

import os
from math import ceil
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request


class ExpensiveRequests:
    def __init__(self):
        self.limit = int(os.getenv("KNOWLEDGE_DESK_EXPENSIVE_REQUESTS_PER_MINUTE", "30"))
        if self.limit < 1:
            raise ValueError("KNOWLEDGE_DESK_EXPENSIVE_REQUESTS_PER_MINUTE must be positive.")
        self.lock = Lock()
        self.window = -1
        self.counts: dict[str, int] = {}

    def acquire(self, user_id: str) -> None:
        with self.lock:
            now = monotonic()
            window = int(now // 60)
            if self.window != window:
                self.window = window
                self.counts.clear()
            used = self.counts.get(user_id, 0)
            if used >= self.limit:
                raise HTTPException(
                    429, {"code": "user_rate_limit", "message": "Лимит запросов на минуту достигнут."},
                    headers={"Retry-After": str(ceil(60 - now % 60))},
                )
            self.counts[user_id] = used + 1


def limit_expensive(request: Request, user_id: str | None) -> None:
    if not user_id:
        raise HTTPException(403, detail={"code": "request_access_denied"})
    request.app.state.expensive_requests.acquire(user_id)

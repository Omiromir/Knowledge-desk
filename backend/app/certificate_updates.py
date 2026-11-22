"""Wake certificate queues, owner lists, and individual cards after a commit."""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from hashlib import sha256
from uuid import uuid4

import psycopg
from fastapi.sse import ServerSentEvent

CHANNEL = "knowledge_desk_certificate_answered"
logger = logging.getLogger(__name__)


def card_topic(request_id: str) -> str:
    return sha256(request_id.encode()).hexdigest()


def owner_topic(user_id: str) -> str:
    return "owner:" + sha256(user_id.encode()).hexdigest()


@dataclass
class CertificateSignal:
    revision: str = field(default_factory=lambda: uuid4().hex)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    subscribers: int = 0

    def publish(self) -> None:
        self.revision = uuid4().hex
        previous, self.changed = self.changed, asyncio.Event()
        previous.set()


class CertificateUpdates:
    def __init__(self) -> None:
        self.queue = CertificateSignal()
        self.scoped: dict[str, CertificateSignal] = {}

    def publish(self, topic: str | None = None) -> None:
        signal = self.queue if topic is None else self.scoped.get(topic)
        if signal is not None:
            signal.publish()

    async def stream(self, topic: str | None = None) -> AsyncIterator[ServerSentEvent]:
        signal = self.queue if topic is None else self.scoped.setdefault(topic, CertificateSignal())
        event = "certificate-queue" if topic is None else (
            "certificate-list" if topic.startswith("owner:") else "certificate-card"
        )
        signal.subscribers += 1
        # Reconnecting rechecks the session and authorization for this scope.
        deadline = asyncio.get_running_loop().time() + 15 * 60
        try:
            while True:
                changed = signal.changed
                yield ServerSentEvent(event=event, raw_data=signal.revision, retry=5000)
                try:
                    async with asyncio.timeout_at(deadline):
                        await changed.wait()
                except TimeoutError:
                    return
        finally:
            signal.subscribers -= 1
            if topic is not None and not signal.subscribers:
                del self.scoped[topic]

    async def listen(self, url: str) -> None:
        delay = 2
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    url, autocommit=True, connect_timeout=5,
                    keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
                ) as connection:
                    await connection.execute(f"LISTEN {CHANNEL}")
                    # Refresh after reconnect so changes made during the gap are visible.
                    self.publish()
                    for signal in self.scoped.values():
                        signal.publish()
                    delay = 2
                    async for notice in connection.notifies():
                        self.publish(notice.payload or None)
            except (psycopg.Error, OSError) as error:
                logger.warning("Certificate notifications disconnected (%s).", type(error).__name__)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

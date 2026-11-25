"""Asynchronous in-process event bus with bounded history and fault isolation."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from bot.core.enums import AlertPriority
from bot.core.models import utc_now

logger = logging.getLogger(__name__)

EventHandler = Callable[["EngineEvent"], Awaitable[None] | None]


@dataclass(frozen=True)
class EngineEvent:
    """Structured internal bus event."""

    topic: str
    priority: AlertPriority
    payload: dict[str, Any]
    timestamp: datetime = field(default_factory=utc_now)


class EventBus:
    """Async pub/sub bus supporting exact topics and '*' wildcard listeners."""

    def __init__(self, history_limit: int = 500) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)
        self._history: deque[EngineEvent] = deque(maxlen=history_limit)

    def subscribe(self, topic: str, handler: EventHandler) -> None:
        self._subscribers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        if topic in self._subscribers:
            self._subscribers[topic] = [h for h in self._subscribers[topic] if h != handler]

    async def publish(self, event: EngineEvent) -> None:
        self._history.append(event)
        handlers = list(self._subscribers.get(event.topic, ())) + list(
            self._subscribers.get("*", ())
        )
        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Unhandled error in event subscriber for topic %s", event.topic)

    def recent_events(self, limit: int = 50) -> list[EngineEvent]:
        if limit <= 0:
            return []
        return list(self._history)[-limit:]

"""Topic-based message bus.

`InProcessBus` mirrors the publish/subscribe shape of the AGNTCY app-sdk transports
(SLIM / NATS group messaging): agents publish to a shared topic and every subscriber
receives every message. The bus only delivers; it never decides anything. Its log is the
public record every agent reasons over.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

TOPIC = "lungo/council/procurement"


@dataclass
class Message:
    round: int
    sender: str
    kind: str  # EVENT | PROPOSAL | VOTE | COMMIT | ESCALATE | STALEMATE | DECISION
    payload: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class InProcessBus:
    def __init__(self) -> None:
        self.log: list[Message] = []
        self._subs: dict[str, list[Callable[[Message], None]]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Callable[[Message], None]) -> None:
        self._subs[topic].append(handler)

    def publish(self, topic: str, msg: Message) -> None:
        self.log.append(msg)
        for handler in self._subs[topic]:
            handler(msg)

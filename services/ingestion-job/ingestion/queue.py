"""Pulling `ingest-museum` messages. Named queue.py as the layout asks; always imported as ingestion.queue."""

from dataclasses import dataclass
from typing import Protocol

PULL_BATCH = 100
MAX_PULLS = 200
ACK_BATCH = 500


@dataclass(frozen=True)
class Received:
    ack_id: str
    data: bytes


class MessageQueue(Protocol):
    def pull_all(self) -> list[Received]:
        """Every pending message, pulled until the subscription is empty."""
        ...

    def ack(self, ack_ids: list[str]) -> None: ...


class NullQueue:
    """Used when MUSEUM_ID is set: Pub/Sub is not touched at all."""

    def pull_all(self) -> list[Received]:
        return []

    def ack(self, ack_ids: list[str]) -> None:
        return None


class PubSubPullQueue:
    def __init__(self, project: str, subscription: str) -> None:
        # Imported here so file mode and tests need no GCP libraries. The client reads
        # PUBSUB_EMULATOR_HOST by itself.
        from google.cloud import pubsub_v1

        self._client = pubsub_v1.SubscriberClient()
        self._path = self._client.subscription_path(project, subscription)

    def pull_all(self) -> list[Received]:
        from google.api_core.exceptions import DeadlineExceeded

        received: list[Received] = []
        for _ in range(MAX_PULLS):
            try:
                response = self._client.pull(
                    request={"subscription": self._path, "max_messages": PULL_BATCH}, timeout=30
                )
            except DeadlineExceeded:
                break
            if not response.received_messages:
                break
            received.extend(Received(m.ack_id, bytes(m.message.data)) for m in response.received_messages)
        return received

    def ack(self, ack_ids: list[str]) -> None:
        for start in range(0, len(ack_ids), ACK_BATCH):
            self._client.acknowledge(
                request={"subscription": self._path, "ack_ids": ack_ids[start : start + ACK_BATCH]}
            )

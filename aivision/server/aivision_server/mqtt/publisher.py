"""MQTT 발행 클라이언트.

수신(`detection/mqtt_source.py`)과 따로 둔 이유
  · 수신은 `async for client.messages` 루프에 묶여 있어 그 연결로 발행을 끼워 넣기 어렵다.
  · 발행이 수신 루프를 막거나, 수신 재접속이 발행을 끊는 일이 없어야 한다.
  · 역할이 다르면 연결도 따로 두는 편이 장애 원인을 좁히기 쉽다.

연결은 한 번 만들어 재사용한다. 끊기면 다음 발행에서 다시 붙는다.
발행 실패는 예외로 알린다 — 호출자(아웃바운드 워커)가 재시도를 관리한다.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import get_settings

log = logging.getLogger(__name__)


class MqttPublisher:
    def __init__(self) -> None:
        self._client = None
        self._lock = asyncio.Lock()

    async def _connect(self):
        import aiomqtt

        s = get_settings()
        client = aiomqtt.Client(
            hostname=s.mqtt_host,
            port=s.mqtt_port,
            identifier=f"{s.mqtt_client_id}-pub",
            username=s.mqtt_username or None,
            password=s.mqtt_password or None,
            keepalive=60,
        )
        await client.__aenter__()
        log.info("MQTT 발행 연결됨: %s:%d", s.mqtt_host, s.mqtt_port)
        return client

    async def publish(self, topic: str, payload: str, *, qos: int = 0,
                      retain: bool = False) -> None:
        """한 건 발행. 실패하면 예외를 던진다(재시도는 호출자 몫)."""
        async with self._lock:
            if self._client is None:
                self._client = await self._connect()
            try:
                await self._client.publish(topic, payload.encode(), qos=qos, retain=retain)
            except Exception:
                # 연결이 죽었을 수 있다. 버리고 다음 호출에서 새로 붙는다.
                await self._drop()
                raise

    async def _drop(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.__aexit__(None, None, None)
        except Exception:                                    # noqa: BLE001
            pass

    async def close(self) -> None:
        async with self._lock:
            await self._drop()


publisher = MqttPublisher()

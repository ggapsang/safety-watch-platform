"""MQTT 인바운드 어댑터.

브로커를 통해 들어오는 모든 메시지를 받아 바인딩 엔진에 넘긴다.
이 파일은 **무엇이 이벤트인지 모른다.** 그건 바인딩 테이블이 안다.

동작
  1. 브로커에 접속해 설정된 토픽(기본 '#')을 구독한다. 끊기면 지수 백오프로 재접속한다.
  2. 수신 원문은 **기본적으로 DB 에 남기지 않는다.** 필요할 때만 설정으로 켠다
     (mqtt_log_mode = off | unmatched | all, mqtt_log_topics 로 특정 채널만).
     기본을 끔으로 두어도 '모르는 토픽 찾기'는 막히지 않는다 — 'MQTT 로그' 화면은
     브로커에 직결 구독하므로 실시간 발견은 DB 와 무관하다. 라이브 박스처럼 초당 여러 번
     들어오는 트래픽을 기본으로 적재하면 하루 수십만 줄이 쌓이고, 정작 원문 로그를 둔
     이유(사후에 되짚기)도 그 잡음에 묻힌다.
  3. 바인딩 엔진에 넘겨 나온 신호를 올린다.
  4. heartbeat 성 토픽은 카메라 생존 신호로만 쓴다.

카메라 엣지든 서버 YOLO 사이드카든 협력사 모듈이든, 브로커로 들어오면 전부 이 통로를 지난다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from datetime import datetime, timezone

from ..config import get_settings
from ..db import sessionmaker
from ..models import Camera
from ..mqtt import onvif
from ..services import raw_log, throttle
from ..services.binding import engine as binding_engine
from .base import DetectionSource

log = logging.getLogger(__name__)

# heartbeat 로 취급할 토픽 꼬리표. 생존 신호일 뿐 이벤트가 아니다.
HEARTBEAT_SUFFIXES = ("/heartbeat", "/keepalive", "/status")

# 원문 로그에서 제외할 토픽(브로커 자체 통계). 안 그러면 로그가 $SYS 로 뒤덮인다.
_NOISE = re.compile(r"^\$SYS/")


class MqttInboundSource(DetectionSource):
    name = "mqtt-inbound"

    def __init__(self) -> None:
        super().__init__()
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self.connected = False
        self.received = 0
        self.matched = 0

    # ------------------------------------------------------------- 수명주기

    async def _start(self) -> None:
        await binding_engine.reload()
        await raw_log.reload()
        await throttle.reload()
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="mqtt-inbound")

    async def _stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self.connected = False

    async def reload(self) -> None:
        await binding_engine.reload()
        await raw_log.reload()
        await throttle.reload()

    def status(self) -> dict:
        return {**super().status(), "connected": self.connected,
                "received": self.received, "matched": self.matched,
                "raw_log": raw_log.status(),
                "throttle": throttle.status(),
                "bindings": binding_engine.count,
                "cameras": binding_engine.camera_count}

    # ------------------------------------------------------------- 수신 루프

    async def _loop(self) -> None:
        import aiomqtt

        s = get_settings()
        backoff = 1.0
        while not self._stopping.is_set():
            try:
                async with aiomqtt.Client(
                    hostname=s.mqtt_host,
                    port=s.mqtt_port,
                    identifier=s.mqtt_client_id,
                    username=s.mqtt_username or None,
                    password=s.mqtt_password or None,
                    keepalive=60,
                ) as client:
                    self.connected = True
                    self.last_error = ""
                    backoff = 1.0
                    await client.subscribe(s.mqtt_subscribe)
                    log.info("MQTT 브로커 연결됨: %s:%d (구독 '%s')",
                             s.mqtt_host, s.mqtt_port, s.mqtt_subscribe)
                    async for message in client.messages:
                        try:
                            await self._handle(str(message.topic), message.payload)
                        except Exception:                          # noqa: BLE001
                            log.exception("MQTT 메시지 처리 실패: %s", message.topic)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                               # noqa: BLE001
                self.connected = False
                self.last_error = str(exc)
                log.warning("MQTT 연결 끊김(%s) — %.0fs 후 재접속", exc, backoff)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=backoff)
                backoff = min(backoff * 2, 30.0)
        self.connected = False

    # ------------------------------------------------------------- 메시지 처리

    async def _handle(self, topic: str, raw: bytes) -> None:
        if _NOISE.match(topic):
            return
        self.received += 1

        # 완충장치. 같은 메시지가 쏟아지면 여기서 끝낸다 — 바인딩도 DB 도 타지 않는다.
        if not throttle.allow(topic, raw):
            return

        payload = onvif.parse_payload(raw)
        mac, rest = onvif.split_topic(topic)
        camera_id = binding_engine.camera_by_mac(mac) if mac else None

        # heartbeat 는 바인딩을 태우지 않는다. 생존 갱신만 하고 끝.
        if camera_id is not None and rest.lower().startswith(HEARTBEAT_SUFFIXES):
            await self._touch_camera(camera_id)
            await raw_log.store_message(topic, raw, camera_id, matched=False)
            return

        signals = binding_engine.apply(topic, payload, transport="mqtt")
        await raw_log.store_message(topic, raw, camera_id, matched=bool(signals))

        if not signals:
            return
        self.matched += len(signals)
        if camera_id is not None:
            await self._touch_camera(camera_id)

        async with sessionmaker()() as session:
            await binding_engine.note_matches(
                session, [s.binding_id for s in signals if s.binding_id])

        for signal in signals:
            await self.emit(signal)

    async def _touch_camera(self, camera_id: int) -> None:
        async with sessionmaker()() as session:
            cam = await session.get(Camera, camera_id)
            if cam is None:
                return
            cam.last_seen_at = datetime.now(timezone.utc)
            cam.online = True
            await session.commit()

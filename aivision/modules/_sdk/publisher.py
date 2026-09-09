"""발행 전용 MQTT 연결.

paho 의 재연결에 맡긴다(loop_start + reconnect_delay_set). 여기서 큐를 만들지 않는 이유:
라이브 박스는 늦게 도착하면 의미가 없고, 전이 이벤트는 유실되면 다음 전이가 있을 뿐이다.
이벤트를 잃지 않는 책임은 플랫폼의 outbox 가 진다 — 모듈이 그것을 흉내 내면 두 곳이
같은 일을 하게 된다.

토픽 모양은 규약일 뿐 특권이 아니다. 서버는 이 토픽을 특별 취급하지 않고, 어드민이
바인딩을 만들어야 이벤트가 된다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

DETECT_TOPIC = "aivision/detect/{camera_id}/{module_id}"
LIVE_TOPIC = "aivision/live/{camera_id}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Publisher:
    def __init__(self, host: str, port: int, client_id: str) -> None:
        import paho.mqtt.client as mqtt

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"{client_id}-pub")
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = lambda *_: log.info("브로커 연결: %s:%d", host, port)
        self.client.on_disconnect = lambda *_: log.warning("브로커 연결 끊김 — 재연결 시도")
        self.host, self.port = host, port

    def start(self) -> None:
        try:
            self.client.connect_async(self.host, self.port, keepalive=60)
            self.client.loop_start()
        except OSError as exc:
            log.warning("브로커 연결 실패(%s) — 계속 재시도합니다", exc)

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def publish(self, topic: str, payload: dict, qos: int = 0) -> None:
        try:
            self.client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=qos)
        except Exception as exc:                                # noqa: BLE001
            log.warning("발행 실패 %s: %s", topic, exc)

    # ── 계약이 정한 두 가지 발행 ────────────────────────────────────

    def publish_detection(self, module_id: str, camera_id: int, item: str, state: str,
                          confidence: float, boxes: list[dict]) -> None:
        self.publish(DETECT_TOPIC.format(camera_id=camera_id, module_id=module_id), {
            "camera_id": camera_id, "module_id": module_id, "item": item, "state": state,
            "ts": now_iso(), "confidence": round(confidence, 3), "boxes": boxes,
        })

    def publish_live(self, module_id: str, camera_id: int, boxes: list[dict]) -> None:
        self.publish(LIVE_TOPIC.format(camera_id=camera_id), {
            "camera_id": camera_id, "module_id": module_id,
            "ts": now_iso(), "boxes": boxes,
        })

"""탐지 소스 레지스트리 — 켜고 끄는 곳.

새 소스를 추가할 때 애플리케이션에서 고쳐야 하는 유일한 파일이다.
소스가 올린 신호는 전부 여기서 이벤트 서비스로 넘어간다.
"""

from __future__ import annotations

import logging

from ..db import sessionmaker
from ..services.events import ingest_signal
from .base import DetectionSignal, DetectionSource
from .mqtt_source import MqttInboundSource

log = logging.getLogger(__name__)


class DetectionRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, DetectionSource] = {}

    def add(self, source: DetectionSource) -> None:
        self._sources[source.name] = source

    def get(self, name: str) -> DetectionSource | None:
        return self._sources.get(name)

    def statuses(self) -> list[dict]:
        return [s.status() for s in self._sources.values()]

    async def start_all(self) -> None:
        for source in self._sources.values():
            try:
                await source.start(_handle_signal)
            except NotImplementedError as exc:
                # 아직 구현되지 않은 소스는 비활성으로 넘긴다. 서버는 계속 뜬다.
                log.info("탐지 소스 '%s' 비활성: %s", source.name, exc)
            except Exception:                                   # noqa: BLE001
                log.exception("탐지 소스 '%s' 기동 실패", source.name)

    async def stop_all(self) -> None:
        for source in self._sources.values():
            if source.running:
                try:
                    await source.stop()
                except Exception:                               # noqa: BLE001
                    log.exception("탐지 소스 '%s' 정지 실패", source.name)

    async def reload_all(self) -> None:
        """카메라·탐지규칙이 바뀌면 호출된다(어드민 저장 직후)."""
        for source in self._sources.values():
            try:
                await source.reload()
            except Exception:                                   # noqa: BLE001
                log.exception("탐지 소스 '%s' 재적재 실패", source.name)


async def _handle_signal(signal: DetectionSignal) -> None:
    async with sessionmaker()() as session:
        await ingest_signal(session, signal)


def build_registry() -> DetectionRegistry:
    reg = DetectionRegistry()
    # 브로커로 들어오는 모든 것(카메라 엣지·사이드카·원격 모듈)이 이 통로를 지난다.
    reg.add(MqttInboundSource())
    # 서버 프로세스 안에서 도는 모듈이 생기면 여기에 등록한다(자기 자신에게 브로커를
    # 거칠 이유가 없다). 서버 YOLO 는 여기 두지 않는다 — 별도 컨테이너에서 모듈 계약을
    # 그대로 쓴다: aivision/modules/yolo.
    return reg


registry = build_registry()

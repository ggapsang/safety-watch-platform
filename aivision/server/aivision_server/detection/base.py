"""탐지 소스 인터페이스.

이 프로젝트의 핵심 경계다. 판정이 **어디서 오든** 서버는 같은 모양의 신호(DetectionSignal)로
받고, 그 뒤 처리(이벤트 적재·스냅샷·화면 통보·통계)는 완전히 동일하다.

  · MqttOnvifSource   — 한화비전 카메라 엣지 앱이 ONVIF 이벤트를 MQTT 로 발행 (현재 주력)
  · ServerYoloSource  — 서버에서 RTSP 프레임을 직접 추론 (껍데기 — 모델 확정 후 구현)
  · (향후) 외부 판정 서비스가 HTTP 로 밀어넣는 소스도 같은 인터페이스로 붙는다.

소스를 추가할 때 건드리는 곳은 여기와 registry 뿐이다. API·DB·프론트는 그대로다.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Box:
    """정규화(0~1) 바운딩 박스."""

    x1: float
    y1: float
    x2: float
    y2: float
    label: str = ""
    score: float = 0.0


@dataclass(slots=True)
class DetectionSignal:
    """정규화된 탐지 신호.

    state 를 둔 이유: 실장비는 탐지 시작(active)과 종료(inactive)를 각각 1회 발행한다.
    'active 로 바뀌는 순간'만 이벤트로 승격하고, inactive 는 상태 해제로만 쓴다.
    """

    camera_id: int
    solution_code: str
    state: str                       # "active" | "inactive"
    ts: datetime
    source: str                      # 통로: mqtt | http | internal | manual
    module_id: str = ""              # 판정 주체: "yolo-ppe", "hanwha-edge" 등
    binding_id: int | None = None    # 이 신호를 만든 바인딩(진단용)
    live_only: bool = False          # True 면 적재하지 않고 라이브로만 흘린다
    confidence: float | None = None
    boxes: list[Box] = field(default_factory=list)
    raw_topic: str = ""
    raw_payload: dict | None = None

    @property
    def is_active(self) -> bool:
        return self.state == "active"


SignalHandler = Callable[[DetectionSignal], Awaitable[None]]


class DetectionSource(ABC):
    """탐지 소스 수명주기."""

    name: str = "base"

    def __init__(self) -> None:
        self._handler: SignalHandler | None = None
        self.running = False
        self.last_error = ""

    async def start(self, handler: SignalHandler) -> None:
        self._handler = handler
        await self._start()
        self.running = True
        log.info("탐지 소스 기동: %s", self.name)

    async def stop(self) -> None:
        await self._stop()
        self.running = False
        log.info("탐지 소스 정지: %s", self.name)

    async def emit(self, signal: DetectionSignal) -> None:
        if self._handler is not None:
            await self._handler(signal)

    async def reload(self) -> None:
        """카메라·탐지규칙이 바뀌었을 때 호출된다. 기본은 아무것도 하지 않는다."""

    def status(self) -> dict:
        return {"name": self.name, "running": self.running, "last_error": self.last_error}

    # ── 하위 구현 ────────────────────────────────────────────────────────

    @abstractmethod
    async def _start(self) -> None: ...

    @abstractmethod
    async def _stop(self) -> None: ...

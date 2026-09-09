"""미디어 백엔드 계약 — 코어가 아는 유일한 미디어 인터페이스.

매니페스토 1번("코어는 버스다. 나머지는 어댑터다")을 미디어 서버 자신에게도 적용한 것이다.
코어는 MediaMTX 를 모른다. `MediaBackend` 만 안다. 과제마다 다른 미디어 서버를 쓰거나
나중에 갈아탈 때 코어를 고치지 않는다.

구현 둘:
  · DirectBackend   — 미디어 서버 없이 카메라 RTSP 를 그대로 쓴다. fan-out 도 녹화도 없다.
  · MediaMTXBackend — 카메라에서 한 번만 당겨 여러 소비자에게 나눠 준다. 녹화·재생 포함.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(slots=True)
class StreamInfo:
    """스트림에 접근하는 법. 소비자마다 필요한 프로토콜이 다르다."""

    stream_id: str                # "cam/1" — 안정적 식별자
    rtsp: str = ""                # 분석 모듈용
    webrtc: str = ""              # 브라우저용(저지연)
    hls: str = ""                 # 브라우저용(호환성)
    ready: bool = False           # 지금 영상이 흐르고 있는가
    recording: bool = False
    detail: str = ""              # 준비 안 됐을 때의 사유


@dataclass(slots=True)
class Segment:
    """녹화 세그먼트 한 조각. 타임라인을 그리는 데 쓴다."""

    start: datetime
    duration_sec: float
    url: str = ""


@dataclass(slots=True)
class BackendHealth:
    name: str
    available: bool
    detail: str = ""
    streams: int = 0
    extra: dict = field(default_factory=dict)


class MediaBackend(ABC):
    """미디어 평면의 계약.

    구현체는 '배관'만 한다. 무엇을 녹화할지·언제 클립을 뽑을지 같은 **정책은 코어의 몫**이고,
    여기서는 시키는 대로 파일을 쓰고 구간을 잘라 줄 뿐이다.
    """

    name: str = "base"
    supports_recording: bool = False

    @abstractmethod
    async def health(self) -> BackendHealth: ...

    @abstractmethod
    async def ensure_stream(self, camera) -> StreamInfo:
        """카메라를 스트림으로 등록하고 접근 정보를 돌려준다. 이미 있으면 갱신한다."""

    @abstractmethod
    async def drop_stream(self, camera_id: int) -> None: ...

    @abstractmethod
    async def stream_info(self, camera_id: int) -> StreamInfo | None: ...

    async def set_recording(self, camera_id: int, enabled: bool,
                            retention_hours: int = 72) -> bool:
        """상시 녹화 on/off. 지원하지 않으면 False."""
        return False

    async def list_segments(self, camera_id: int, start: datetime,
                            end: datetime) -> list[Segment]:
        return []

    async def export_clip(self, camera_id: int, start: datetime, duration_sec: float,
                          out_path: Path) -> Path | None:
        """구간을 별도 파일로 뽑아낸다.

        주의: 이벤트 클립은 반드시 이렇게 **복사해 내야** 한다. 세그먼트에 참조만 걸어 두면
          보존기간이 지나 회전 삭제될 때 사고 영상이 같이 사라진다. 상시 녹화(며칠)와
          사고 클립(길게)은 보존 정책이 다르다.
        """
        return None

    async def close(self) -> None:
        return None


class DirectBackend(MediaBackend):
    """미디어 서버 없이 카메라에 직접 붙는다.

    가장 단순하지만 fan-out 이 없다. 카메라의 동시 RTSP 세션 한계(보통 5~10) 안에서만
    소비자를 붙일 수 있고, 녹화·재생도 없다. 개발 중이거나 미디어 서버를 못 쓰는 현장용.
    """

    name = "direct"
    supports_recording = False

    async def health(self) -> BackendHealth:
        return BackendHealth(self.name, True, "미디어 서버 없이 카메라에 직접 접속합니다")

    async def ensure_stream(self, camera) -> StreamInfo:
        from ..streaming.manager import rtsp_url

        return StreamInfo(stream_id=f"cam/{camera.id}", rtsp=rtsp_url(camera), ready=True,
                          detail="카메라 직접 접속 — fan-out·녹화 없음")

    async def drop_stream(self, camera_id: int) -> None:
        return None

    async def stream_info(self, camera_id: int) -> StreamInfo | None:
        return None

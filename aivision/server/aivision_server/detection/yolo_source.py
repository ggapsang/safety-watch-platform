"""서버사이드 YOLO 탐지 소스 — **껍데기**.

모델이 확정되지 않아 추론부를 비워 뒀다. 다만 '나중에 끼워 넣기'가 아니라
지금 당장 자리를 정확히 잡아 두는 것이 목적이다. 그래서 다음은 이미 정해져 있다.

  · 입력   : StreamManager 의 CameraWorker.latest_frame() (RTSP 원본 BGR 프레임)
  · 출력   : DetectionSignal (다른 소스와 완전히 동일) — 박스는 0~1 정규화 좌표
  · 실행   : 카메라별 워커 태스크. 전 프레임을 추론하지 않고 sample_fps 로 솎는다.
  · 전이   : 연속 프레임의 탐지 유무를 hold_sec 창으로 눌러 active/inactive 를 만든다.
             (매 프레임 이벤트를 만들면 DB 가 터진다 — MQTT 소스와 같은 계약을 유지한다.)

구현할 때 참고할 자산은 refs/legacy-iseco-pc/app/detector.py 에 있다.
재사용 가능: letterbox / xywh2xyxy / nms / _scale_back / ONNX·TorchScript 백엔드 로더.
재사용 불가: 그 파일의 후처리는 4클래스를 ReduceMax 로 뭉갠 단일 클래스 전용 export 에
   맞춰져 있다(yolov7 앵커 하드코딩 포함). 새 모델은 다중 클래스 헤드로 다시 써야 한다.
"""

from __future__ import annotations

import asyncio
import logging

from .base import DetectionSource

log = logging.getLogger(__name__)


class ServerYoloSource(DetectionSource):
    """아직 켜지지 않는 소스. 활성화하려면 모델과 후처리를 먼저 확정해야 한다."""

    name = "server-yolo"

    def __init__(self, *, model_path: str = "", sample_fps: float = 4.0,
                 hold_sec: float = 3.0, conf_threshold: float = 0.35) -> None:
        super().__init__()
        self.model_path = model_path
        self.sample_fps = sample_fps
        self.hold_sec = hold_sec
        self.conf_threshold = conf_threshold
        self._task: asyncio.Task | None = None

    async def _start(self) -> None:
        self.last_error = "미구현 — 모델 파일과 후처리가 확정되지 않았습니다"
        log.warning(
            "서버사이드 YOLO 소스는 아직 껍데기입니다. 현재 탐지는 카메라 엣지(MQTT/ONVIF)가 "
            "전담합니다. 활성화하려면 %s 의 추론부를 구현하세요.", __name__)
        raise NotImplementedError(self.last_error)

    async def _stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    # ── 구현 지점 (시그니처만 고정해 둔다) ──────────────────────────────

    def load_model(self) -> None:
        """ONNX Runtime / TorchScript 백엔드 로드. device 는 cuda→cpu 자동 폴백."""
        raise NotImplementedError

    def infer(self, frame) -> list:                              # -> list[Box]
        """전처리(letterbox) → 추론 → 후처리(conf/NMS/좌표 역변환/정규화).

        반환 좌표계는 **0~1 정규화**여야 한다. 픽셀로 반환하지 말 것 —
        해상도가 다른 카메라가 섞이면 화면에서 박스가 어긋난다.
        """
        raise NotImplementedError

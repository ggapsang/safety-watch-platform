"""추론 루프 <-> 대시보드 사이의 공유 상태(스냅샷).

추론 루프만 write, 대시보드 스레드는 read 만 한다. 모든 접근은 lock 으로 보호.
(⚠ XGT 클라이언트는 thread-safe 가 아니므로 대시보드는 PLC 를 직접 만지지 않고
  여기 기록된 상태만 읽는다.)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Snapshot:
    running: bool = False
    started_at: float = field(default_factory=time.time)
    video_connected: bool = False
    video_reconnects: int = 0
    plc_connected: bool = False
    plc_enabled: bool = False
    plc_last_write: str = "-"
    plc_last_error: str = ""
    plc_write_ok: int = 0
    plc_write_fail: int = 0
    in_fault: bool = False
    fault_reason: str = ""

    fps: float = 0.0
    infer_ms: float = 0.0
    det_count: int = 0
    area_value: float = 0.0
    area_unit: str = "percent"

    max_count: int = 0
    max_area: float = 0.0
    category: str = "L_L"
    severity: int = 0
    severity_label: str = "정상"
    last_sent_code: int | None = None
    last_sent_at: float | None = None
    decision_reason: str = "-"

    backend: str = ""
    device: str = ""
    infer_mode: str = ""


class SharedState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap = Snapshot()
        self._jpeg: bytes | None = None
        self._jpeg_seq = 0

    def update(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if not hasattr(self._snap, k):
                    raise AttributeError(f"Snapshot 에 없는 필드: {k}")
                setattr(self._snap, k, v)

    def snapshot(self) -> Snapshot:
        with self._lock:
            return Snapshot(**vars(self._snap))

    def set_jpeg(self, data: bytes) -> None:
        with self._lock:
            self._jpeg = data
            self._jpeg_seq += 1

    def get_jpeg(self) -> tuple[int, bytes | None]:
        with self._lock:
            return self._jpeg_seq, self._jpeg


def encode_jpeg(frame: np.ndarray, quality: int = 75) -> bytes | None:
    import cv2

    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return buf.tobytes() if ok else None

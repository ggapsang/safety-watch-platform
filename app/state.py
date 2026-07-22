"""추론 루프 <-> 대시보드 사이의 공유 상태(스냅샷).

추론 루프만 write, 대시보드 스레드는 read 만 한다. 모든 접근은 lock 으로 보호.
(⚠ XGT 클라이언트는 thread-safe 가 아니므로 대시보드는 PLC 를 직접 만지지 않고
  여기 기록된 상태만 읽는다.)
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

MAX_LOG_LINES = 400
MAX_CAPTURES = 8          # 최신 8장만 유지하고 오래된 것부터 밀어낸다


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


@dataclass
class Capture:
    """'마지막으로 판독한' 라벨 걸린 캡처 1장."""

    id: int
    ts: float
    code: int
    label: str
    category: str
    det_count: int
    top_score: float
    raw_label: str
    jpeg: bytes = b""


class SharedState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap = Snapshot()
        self._jpeg: bytes | None = None
        self._jpeg_seq = 0
        self._logs: deque[dict] = deque(maxlen=MAX_LOG_LINES)
        self._log_seq = 0
        self._captures: deque[Capture] = deque(maxlen=MAX_CAPTURES)
        self._capture_seq = 0

    # ------------------------------------------------------------------- 로그

    def add_log(self, level: str, msg: str, source: str = "app") -> None:
        with self._lock:
            self._log_seq += 1
            self._logs.append({
                "seq": self._log_seq,
                "t": time.strftime("%H:%M:%S"),
                "level": level,
                "source": source,
                "msg": msg,
            })

    def logs_since(self, since: int = 0, limit: int = 200) -> list[dict]:
        with self._lock:
            return [e for e in self._logs if e["seq"] > since][-limit:]

    # ------------------------------------------------------------------- 캡처

    def add_capture(self, jpeg: bytes, **meta) -> None:
        with self._lock:
            self._capture_seq += 1
            self._captures.append(Capture(id=self._capture_seq, ts=time.time(),
                                          jpeg=jpeg, **meta))

    def capture_list(self) -> list[dict]:
        """최신순 메타데이터(이미지 바이트 제외)."""
        with self._lock:
            return [{k: v for k, v in vars(c).items() if k != "jpeg"}
                    for c in reversed(self._captures)]

    def capture_jpeg(self, capture_id: int) -> bytes | None:
        with self._lock:
            for c in self._captures:
                if c.id == capture_id:
                    return c.jpeg
        return None

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


def encode_jpeg(frame: np.ndarray, quality: int = 75, max_width: int = 0) -> bytes | None:
    import cv2

    if max_width and frame.shape[1] > max_width:
        scale = max_width / frame.shape[1]
        frame = cv2.resize(frame, (max_width, int(round(frame.shape[0] * scale))),
                           interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return buf.tobytes() if ok else None


class StateLogHandler(logging.Handler):
    """앱 로그를 대시보드 로그 패널로 흘려보낸다."""

    def __init__(self, state: SharedState) -> None:
        super().__init__()
        self.state = state

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.state.add_log(record.levelname, record.getMessage(),
                               record.name.replace("app.", ""))
        except Exception:                                       # noqa: BLE001
            pass

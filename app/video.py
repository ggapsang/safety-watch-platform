"""영상 수신 — RTSP(또는 파일/웹캠). 최신 프레임 우선, 끊김 시 자동 재연결.

실시간성 > 무손실. 별도 스레드가 계속 grab 하여 항상 '가장 최근 프레임'만 보관한다.
"""

from __future__ import annotations

import logging
import os
import threading
import time

import numpy as np

log = logging.getLogger(__name__)


class VideoSource:
    def __init__(self, source: str, reconnect_sec: float = 3.0,
                 read_timeout_sec: float = 10.0, ffmpeg_options: str = "") -> None:
        if not source:
            raise ValueError("VIDEO_SOURCE 가 비어 있습니다 (RTSP URL / 파일 경로 / 웹캠 인덱스)")
        self.source = source
        self.reconnect_sec = reconnect_sec
        self.read_timeout_sec = read_timeout_sec
        self.ffmpeg_options = ffmpeg_options

        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._frame_id = 0
        self._last_frame_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = False
        self.reconnects = 0
        # 로컬 파일이면 EOF 에서 재연결 대신 처음으로 되감아 반복 재생한다(개발/시연용).
        self.is_file = not source.isdigit() and "://" not in source and os.path.exists(source)

    # ------------------------------------------------------------------ 수명주기

    def start(self) -> "VideoSource":
        if self.ffmpeg_options:
            # OpenCV FFmpeg 백엔드 옵션 (지연 누적 방지). VideoCapture 생성 전에 설정해야 적용됨.
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", self.ffmpeg_options)
        self._thread = threading.Thread(target=self._run, name="video", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def __enter__(self) -> "VideoSource":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------------ 내부 루프

    def _open(self):
        import cv2

        src: str | int = self.source
        if isinstance(src, str) and src.isdigit():
            src = int(src)                      # 웹캠 인덱스
        cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG if isinstance(src, str) else cv2.CAP_ANY)
        if cap.isOpened():
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)     # 지연 누적 방지
            except Exception:
                pass
        return cap

    def _run(self) -> None:
        cap = None
        while not self._stop.is_set():
            if cap is None or not cap.isOpened():
                if cap is not None:
                    cap.release()
                    self.reconnects += 1
                cap = self._open()
                if not cap.isOpened():
                    self.connected = False
                    log.warning("영상 소스 열기 실패 — %.1fs 후 재시도: %s", self.reconnect_sec, self.source)
                    self._stop.wait(self.reconnect_sec)
                    continue
                self.connected = True
                self._last_frame_at = time.monotonic()
                log.info("영상 소스 연결됨: %s", self.source)

            ok, frame = cap.read()
            now = time.monotonic()
            if not ok or frame is None:
                if self.is_file:
                    import cv2

                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)     # EOF -> 되감아 반복 재생
                    self._last_frame_at = now
                    continue
                if now - self._last_frame_at > self.read_timeout_sec:
                    log.warning("영상 수신 타임아웃(%.1fs) — 재연결", self.read_timeout_sec)
                    cap.release()
                    cap = None
                    self.connected = False
                    continue
                time.sleep(0.01)
                continue

            self._last_frame_at = now
            with self._lock:
                self._frame = frame
                self._frame_id += 1

        if cap is not None:
            cap.release()
        self.connected = False

    # ------------------------------------------------------------------ 소비자 API

    def read_latest(self, last_id: int = -1) -> tuple[int, np.ndarray | None]:
        """(frame_id, frame). last_id 와 같으면 새 프레임이 없다는 뜻."""
        with self._lock:
            if self._frame is None or self._frame_id == last_id:
                return self._frame_id, None
            return self._frame_id, self._frame

    @property
    def stale_sec(self) -> float:
        return time.monotonic() - self._last_frame_at if self._last_frame_at else float("inf")

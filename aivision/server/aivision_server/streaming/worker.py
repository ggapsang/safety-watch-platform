"""RTSP 수신 워커 — 카메라 1대당 1개.

레거시 앱(refs/legacy-iseco-pc/app/video.py)에서 검증된 부분을 그대로 가져왔다.
  · 별도 스레드가 계속 grab 하고 '가장 최근 프레임'만 보관한다(실시간성 > 무손실).
  · 끊기면 자동 재연결. 파일 소스는 EOF 에서 되감아 반복(개발·시연용).
  · CAP_PROP_BUFFERSIZE=1 + rtsp_transport=tcp 로 지연 누적을 막는다.

여기에 더한 것
  · JPEG 인코딩을 워커가 **한 번만** 수행하고 결과를 공유한다. 시청자가 몇 명이든
    인코딩 비용은 1회다(MJPEG 로 2대·1~2명을 감당하는 근거).
  · 오버레이(박스)는 프레임에 굽지 않는다. 박스는 별도 채널(WebSocket)로 나가고
    브라우저가 <img> 위에 겹쳐 그린다 — 영상 전송 방식을 나중에 바꿔도 오버레이는 그대로다.
"""

from __future__ import annotations

import logging
import os
import threading
import time

import numpy as np

log = logging.getLogger(__name__)


class CameraWorker:
    def __init__(self, camera_id: int, source: str, *, label: str = "",
                 reconnect_sec: float = 3.0, read_timeout_sec: float = 10.0,
                 ffmpeg_options: str = "", jpeg_quality: int = 75,
                 max_width: int = 1280, target_fps: float = 12.0) -> None:
        self.camera_id = camera_id
        self.source = source
        self.label = label or f"cam{camera_id}"
        self.reconnect_sec = reconnect_sec
        self.read_timeout_sec = read_timeout_sec
        self.ffmpeg_options = ffmpeg_options
        self.jpeg_quality = jpeg_quality
        self.max_width = max_width
        self.min_interval = 1.0 / max(1.0, target_fps)

        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None      # 원본 프레임(스냅샷·추론용)
        self._jpeg: bytes | None = None            # 송출용 JPEG
        self._seq = 0
        self._new_jpeg = threading.Condition(self._lock)
        self._last_frame_at = 0.0
        self._last_encode_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self.connected = False
        self.reconnects = 0
        self.last_error = ""
        self.width = 0
        self.height = 0
        self.fps = 0.0

        self.is_file = ("://" not in source) and os.path.exists(source)

    # ------------------------------------------------------------- 수명주기

    def start(self) -> "CameraWorker":
        if self._thread is not None:
            return self
        if self.ffmpeg_options:
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", self.ffmpeg_options)
        self._thread = threading.Thread(target=self._run, name=f"cam-{self.camera_id}",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._new_jpeg.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=4.0)
        self._thread = None

    # ------------------------------------------------------------- 내부 루프

    def _open(self):
        import cv2

        src: str | int = self.source
        if isinstance(src, str) and src.isdigit():
            src = int(src)                       # 웹캠 인덱스(개발용)
        cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG if isinstance(src, str) else cv2.CAP_ANY)
        if cap.isOpened():
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
        return cap

    def _run(self) -> None:
        import cv2

        cap = None
        last_t = time.perf_counter()
        while not self._stop.is_set():
            if cap is None or not cap.isOpened():
                if cap is not None:
                    cap.release()
                    self.reconnects += 1
                cap = self._open()
                if not cap.isOpened():
                    self.connected = False
                    self.last_error = "영상 소스 열기 실패"
                    log.warning("[%s] 영상 소스 열기 실패 — %.1fs 후 재시도", self.label,
                                self.reconnect_sec)
                    self._stop.wait(self.reconnect_sec)
                    continue
                self.connected = True
                self.last_error = ""
                self._last_frame_at = time.monotonic()
                log.info("[%s] 영상 소스 연결됨", self.label)

            ok, frame = cap.read()
            now = time.monotonic()
            if not ok or frame is None:
                if self.is_file:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    self._last_frame_at = now
                    continue
                if now - self._last_frame_at > self.read_timeout_sec:
                    self.last_error = f"영상 수신 타임아웃({self.read_timeout_sec:.0f}s)"
                    log.warning("[%s] %s — 재연결", self.label, self.last_error)
                    cap.release()
                    cap = None
                    self.connected = False
                    continue
                time.sleep(0.01)
                continue

            self._last_frame_at = now
            self.height, self.width = frame.shape[:2]

            t = time.perf_counter()
            dt = t - last_t
            last_t = t
            if dt > 0:
                self.fps = 0.9 * self.fps + 0.1 * (1.0 / dt) if self.fps else 1.0 / dt

            # 송출 상한을 넘지 않게 인코딩 빈도를 제한한다(원본이 30fps 여도 12fps 로만 내보낸다).
            encode = (now - self._last_encode_at) >= self.min_interval
            jpeg = self._encode(frame) if encode else None
            if encode:
                self._last_encode_at = now

            with self._lock:
                self._frame = frame
                if jpeg is not None:
                    self._jpeg = jpeg
                    self._seq += 1
                    self._new_jpeg.notify_all()

        if cap is not None:
            cap.release()
        self.connected = False

    def _encode(self, frame: np.ndarray) -> bytes | None:
        import cv2

        if self.max_width and frame.shape[1] > self.max_width:
            scale = self.max_width / frame.shape[1]
            frame = cv2.resize(frame, (self.max_width, int(round(frame.shape[0] * scale))),
                               interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        return buf.tobytes() if ok else None

    # ------------------------------------------------------------- 소비자 API

    def wait_jpeg(self, last_seq: int, timeout: float = 2.0) -> tuple[int, bytes | None]:
        """새 JPEG 이 생길 때까지 기다렸다 반환한다.

        폴링 대신 Condition 을 쓰는 이유: 폴링은 CPU 를 태우고, sleep 간격만큼 지연이 붙는다.
        (호출자는 스레드풀에서 부르므로 블로킹해도 이벤트 루프를 막지 않는다.)
        """
        with self._lock:
            if self._seq == last_seq:
                self._new_jpeg.wait(timeout)
            if self._jpeg is None:
                return self._seq, None
            return self._seq, self._jpeg

    def latest_jpeg(self) -> tuple[int, bytes | None]:
        with self._lock:
            return self._seq, self._jpeg

    def latest_frame(self) -> np.ndarray | None:
        """스냅샷 저장·추론용 원본 프레임(BGR)."""
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def snapshot_jpeg(self, quality: int = 82) -> bytes | None:
        """현재 프레임을 JPEG 으로. 아직 수신된 프레임이 없으면 None.

        (프레임 확인을 cv2 import 보다 먼저 한다 — 영상이 한 장도 안 들어온 상태에서
         디코더 라이브러리 로드까지 갈 이유가 없다.)
        """
        frame = self.latest_frame()
        if frame is None:
            return None
        import cv2

        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return buf.tobytes() if ok else None

    @property
    def stale_sec(self) -> float:
        return time.monotonic() - self._last_frame_at if self._last_frame_at else float("inf")

    def status(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "connected": self.connected,
            "reconnects": self.reconnects,
            "fps": round(self.fps, 1),
            "width": self.width,
            "height": self.height,
            "stale_sec": round(self.stale_sec, 1),
            "last_error": self.last_error,
        }

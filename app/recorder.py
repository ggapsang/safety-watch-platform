"""영상 녹화 — 최대 N초(기본 60초)까지 MP4 로 저장하고 다운로드할 수 있게 한다.

메모리에 프레임을 쌓지 않고 곧바로 파일로 흘린다.
(4K 60초를 원본 그대로 메모리에 담으면 수 GB — 비현실적)

⚠ 추론 루프(단일 스레드)에서만 write() 를 호출한다. 시작/정지 요청은 대시보드
   스레드에서 오므로 lock 으로 보호한다.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MAX_SECONDS_CAP = 60.0        # 기획: 최대 1분
KEEP_FILES = 5                # 오래된 녹화는 밀어낸다
RECORD_WIDTH = 1280           # 4K 원본을 그대로 쓰면 파일이 과도하게 커진다


class Recorder:
    def __init__(self, out_dir: Path, keep: int = KEEP_FILES) -> None:
        self.dir = Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.keep = keep
        self._lock = threading.Lock()
        self._writer = None
        self._path: Path | None = None
        self._started_at = 0.0
        self._limit = MAX_SECONDS_CAP
        self._frames = 0
        self._size: tuple[int, int] | None = None
        self._pending_open = False
        self._fps = 15.0
        self.overlay = True
        self.last_error = ""

    # ------------------------------------------------------------------ 상태

    @property
    def active(self) -> bool:
        return self._writer is not None or self._pending_open

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started_at if self._started_at else 0.0

    def status(self) -> dict:
        return {
            "active": self.active,
            "elapsed": round(self.elapsed, 1),
            "limit": self._limit,
            "frames": self._frames,
            "name": self._path.name if self._path else "",
            "overlay": self.overlay,
            "error": self.last_error,
        }

    # ------------------------------------------------------------------ 제어

    def start(self, seconds: float = MAX_SECONDS_CAP, fps: float = 15.0,
              overlay: bool = True) -> dict:
        with self._lock:
            if self.active:
                raise ValueError("이미 녹화 중입니다")
            self._limit = max(1.0, min(float(seconds), MAX_SECONDS_CAP))
            self.overlay = bool(overlay)
            self._fps = max(5.0, min(float(fps), 30.0))
            self._path = self.dir / f"rec_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
            self._writer = None          # 첫 프레임에서 크기를 알고 나서 연다
            self._size = None
            self._frames = 0
            self._started_at = time.monotonic()
            self._pending_open = True
            self.last_error = ""
            log.info("녹화 시작: %s (최대 %.0f초, %.0f fps, 오버레이=%s)",
                     self._path.name, self._limit, self._fps, self.overlay)
            return self.status()

    def _open(self, frame: np.ndarray) -> None:
        import cv2

        h, w = frame.shape[:2]
        scale = min(1.0, RECORD_WIDTH / w)
        self._size = (int(w * scale), int(h * scale))
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(self._path), fourcc, self._fps, self._size)
        if not writer.isOpened():
            self.last_error = "VideoWriter 를 열 수 없습니다(코덱 확인)"
            log.error("녹화 실패 — %s", self.last_error)
            self._pending_open = False
            self._path = None
            return
        self._writer = writer
        self._pending_open = False

    def write(self, frame: np.ndarray) -> None:
        """추론 루프에서 매 프레임 호출. 녹화 중이 아니면 즉시 반환."""
        with self._lock:
            if self._pending_open:
                self._open(frame)
            if self._writer is None:
                return
            if self.elapsed >= self._limit:
                self._stop_locked("최대 길이 도달")
                return
            import cv2

            if self._size and (frame.shape[1], frame.shape[0]) != self._size:
                frame = cv2.resize(frame, self._size, interpolation=cv2.INTER_AREA)
            self._writer.write(frame)
            self._frames += 1

    def stop(self, reason: str = "사용자 정지") -> dict:
        with self._lock:
            return self._stop_locked(reason)

    def _stop_locked(self, reason: str) -> dict:
        self._pending_open = False
        if self._writer is None:
            self._started_at = 0.0
            return self.status()
        path, frames, secs = self._path, self._frames, self.elapsed
        self._writer.release()
        self._writer = None
        self._started_at = 0.0
        log.info("녹화 종료(%s): %s — %d 프레임 %.1f초", reason,
                 path.name if path else "?", frames, secs)
        self._path = None
        self._prune()
        return {"active": False, "name": path.name if path else "", "frames": frames,
                "seconds": round(secs, 1)}

    def _prune(self) -> None:
        files = sorted(self.dir.glob("rec_*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[self.keep:]:
            try:
                old.unlink()
                log.info("오래된 녹화 삭제: %s", old.name)
            except OSError:
                pass

    # ------------------------------------------------------------------ 목록

    def listing(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("rec_*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
            st = p.stat()
            out.append({"name": p.name, "bytes": st.st_size, "ts": st.st_mtime,
                        "recording": self.active and self._path is not None
                        and p.name == self._path.name})
        return out

    def file_path(self, name: str) -> Path | None:
        """경로 조작 방지 — 녹화 디렉터리 안의 rec_*.mp4 만 허용."""
        if "/" in name or "\\" in name or not name.startswith("rec_") or not name.endswith(".mp4"):
            return None
        p = (self.dir / name).resolve()
        if p.parent != self.dir.resolve() or not p.exists():
            return None
        return p

"""카메라 워커 레지스트리.

DB 의 cameras 테이블이 진실이고, 매니저는 거기에 맞춰 워커를 띄우고 내린다.
어드민에서 카메라를 등록/수정/삭제하면 `sync()` 가 호출되어 즉시 반영된다.

워커는 **미디어 백엔드가 주는 스트림**에서 프레임을 받는다. 카메라에 직접 붙지 않는다.
미디어 서버를 쓰면 카메라에는 연결이 하나만 생기고(fan-out), 워커는 그중 한 소비자일 뿐이다.
워커가 하는 일은 스냅샷·내장 추론용 프레임 공급과 브라우저 MJPEG 송출이다.

미디어 서버가 죽어 있으면 카메라 RTSP 로 되돌아간다 — 어댑터가 하나 없다고 코어가 멈추면
안 된다(매니페스토 1번).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..crypto import decrypt
from ..models import Camera
from .worker import CameraWorker

log = logging.getLogger(__name__)


def rtsp_url(cam: Camera, *, sub: bool = False) -> str:
    """카메라 레코드 → RTSP URL. 자격증명은 URL 인코딩한다(비밀번호에 @, / 가 흔하다).

    sub=True 면 보조(저화질) 스트림. 경로가 비어 있으면 빈 문자열을 돌려준다 —
    '없다' 와 '주 스트림과 같다' 는 다르다. 같다고 보면 저화질인 줄 알고 4K 를 준다.
    """
    path = (cam.rtsp_path_sub if sub else cam.rtsp_path) or ("" if sub else "/")
    if not path:
        return ""
    pw = decrypt(cam.password_enc)
    cred = ""
    if cam.username:
        cred = f"{quote(cam.username, safe='')}:{quote(pw, safe='')}@"
    port = cam.rtsp_port or 554
    host = f"{cam.ip}:{port}" if port != 554 else cam.ip
    if not path.startswith("/"):
        path = "/" + path
    return f"rtsp://{cred}{host}{path}"


def masked_rtsp_url(cam: Camera) -> str:
    """로그·화면 표시용 — 비밀번호를 가린 RTSP URL."""
    port = cam.rtsp_port or 554
    host = f"{cam.ip}:{port}" if port != 554 else cam.ip
    cred = f"{cam.username}:****@" if cam.username else ""
    return f"rtsp://{cred}{host}{cam.rtsp_path or '/'}"


class StreamManager:
    def __init__(self) -> None:
        self._workers: dict[int, CameraWorker] = {}
        self._sources: dict[int, str] = {}      # 접속 정보 변경 감지용
        self._stream_info: dict[int, object] = {}
        self._lock = asyncio.Lock()

    def get(self, camera_id: int) -> CameraWorker | None:
        return self._workers.get(camera_id)

    def statuses(self) -> dict[int, dict]:
        return {cid: w.status() for cid, w in self._workers.items()}

    async def sync(self, session: AsyncSession) -> None:
        """DB 상태에 맞춰 미디어 경로와 워커를 기동/정지/재기동한다."""
        from ..media import backend as media_backend

        s = get_settings()
        media = media_backend()
        rows = (await session.execute(select(Camera))).scalars().all()
        labels = {c.id: f"{c.name}({c.location})" for c in rows}

        wanted: dict[int, str] = {}
        for cam in rows:
            if not cam.enabled:
                continue
            wanted[cam.id] = await self._source_for(media, cam)

        # 더는 쓰지 않는 카메라는 미디어 경로에서도 내린다.
        gone = set(self._sources) - set(wanted)
        for cid in gone:
            with contextlib.suppress(Exception):
                await media.drop_stream(cid)

        async with self._lock:
            # 사라졌거나 접속 정보가 바뀐 워커 정리
            for cid in list(self._workers):
                if cid not in wanted or self._sources.get(cid) != wanted[cid]:
                    log.info("카메라 워커 정지: %s", labels.get(cid, cid))
                    await asyncio.to_thread(self._workers.pop(cid).stop)
                    self._sources.pop(cid, None)

            # 새로 필요한 워커 기동
            for cid, url in wanted.items():
                if cid in self._workers:
                    continue
                log.info("카메라 워커 기동: %s", labels.get(cid, cid))
                worker = CameraWorker(
                    cid, url, label=labels.get(cid, str(cid)),
                    reconnect_sec=s.rtsp_reconnect_sec,
                    read_timeout_sec=s.rtsp_read_timeout_sec,
                    ffmpeg_options=s.rtsp_ffmpeg_options,
                    jpeg_quality=s.jpeg_quality,
                    max_width=s.stream_max_width,
                    target_fps=s.stream_fps,
                )
                worker.start()
                self._workers[cid] = worker
                self._sources[cid] = url

    async def _source_for(self, media, cam: Camera) -> str:
        """워커가 붙을 주소. 미디어 서버가 살아 있으면 그쪽, 아니면 카메라 직접."""
        try:
            info = await media.ensure_stream(cam)
        except Exception:                                    # noqa: BLE001
            log.warning("미디어 경로 등록 실패(#%d) — 카메라에 직접 붙습니다", cam.id,
                        exc_info=True)
            return rtsp_url(cam)
        if info.rtsp:
            self._stream_info[cam.id] = info
            return info.rtsp
        log.warning("미디어 스트림 주소를 얻지 못했습니다(#%d): %s — 카메라 직접 접속",
                    cam.id, info.detail)
        return rtsp_url(cam)

    def stream_info(self, camera_id: int):
        """마지막으로 확인된 스트림 접근 정보(분석 모듈에 넘겨 줄 주소)."""
        return self._stream_info.get(camera_id)

    async def shutdown(self) -> None:
        async with self._lock:
            for cid, worker in list(self._workers.items()):
                await asyncio.to_thread(worker.stop)
                self._workers.pop(cid, None)
            self._sources.clear()


manager = StreamManager()

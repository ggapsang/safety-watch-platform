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
import time
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
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
    cred = ""
    if cam.username:
        cred = f"{quote(cam.username, safe='')}:{quote(cam.password, safe='')}@"
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


# 마지막 시청자가 떠난 뒤 고화질 워커를 이만큼 더 붙잡아 둔다.
#
# 카메라를 이리저리 눌러 보는 동안 매번 RTSP 를 새로 여는 것을 막는다. 다시 붙는 데
# 1~2초가 걸려서, 이 유예가 없으면 화면을 옮길 때마다 검은 칸을 보게 된다.
# 이벤트 스냅샷도 이 온기를 같이 쓴다 — 한 번 열어 두면 연달아 터지는 판정이 재사용한다.
MAIN_IDLE_SEC = 25.0


class StreamManager:
    """카메라마다 워커를 **화질별로** 따로 둔다.

    예전에는 카메라당 워커 하나가 원본을 디코딩해 모든 용도에 나눠 줬다. 그런데 화면에
    실제로 크게 뜨는 것은 한 대뿐이고 나머지는 목록의 작은 썸네일이다 — 썸네일 하나
    띄우려고 2560×1440 을 초당 30장 푸는 셈이었다(카메라 6대에 CPU 523%).

    그래서 둘로 나눈다.
      · 상시 워커   저화질(없으면 원본). 썸네일·상태 판정·모듈 프레임 공급.
      · 고화질 워커  크게 띄운 화면(MJPEG)과 이벤트 스냅샷. **볼 때만** 뜬다.

    켜고 끄는 신호는 MJPEG 연결 그 자체다. 브라우저가 물고 있는 HTTP 스트림이라 붙고
    끊기는 순간이 정확하다 — 예전에 '보고 있는 카메라' 를 주기 보고로 추측했다가
    값이 워커에 고정돼 버린 적이 있다. 소켓은 추측하지 않는다.
    """

    def __init__(self) -> None:
        self._workers: dict[int, CameraWorker] = {}
        self._sources: dict[int, str] = {}      # 접속 정보 변경 감지용
        # 고화질 워커와 그 수명. holders 는 지금 보고 있는 연결 수다.
        self._main: dict[int, CameraWorker] = {}
        self._main_holders: dict[int, int] = {}
        self._main_idle_at: dict[int, float] = {}
        self._urls: dict[int, tuple[str, str]] = {}   # 카메라 -> (원본, 저화질)
        self._stream_info: dict[int, object] = {}
        self._lock = asyncio.Lock()

    def get(self, camera_id: int) -> CameraWorker | None:
        """상시 워커. 썸네일·상태 판정이 쓰는 값이라 늘 살아 있다."""
        return self._workers.get(camera_id)

    def get_main(self, camera_id: int) -> CameraWorker | None:
        """지금 떠 있는 고화질 워커. 아무도 안 보고 있으면 None."""
        return self._main.get(camera_id)

    def statuses(self) -> dict[int, dict]:
        return {cid: w.status() for cid, w in self._workers.items()}

    # ── 고화질 워커 (볼 때만) ────────────────────────────────────────

    async def acquire_main(self, camera_id: int) -> CameraWorker | None:
        """고화질 워커를 띄우고(이미 떠 있으면 그대로) 한 자리 잡는다.

        반드시 `release_main` 과 짝지어 부른다 — 안 놓으면 그 카메라는 영원히 원본을
        디코딩한다. 저화질 경로가 없는 카메라는 상시 워커가 이미 원본이므로 그것을
        그대로 돌려준다(두 번 디코딩할 이유가 없다).
        """
        async with self._lock:
            main_url, sub_url = self._urls.get(camera_id, ("", ""))
            if not main_url or not sub_url:
                return self._workers.get(camera_id)

            self._main_holders[camera_id] = self._main_holders.get(camera_id, 0) + 1
            self._main_idle_at.pop(camera_id, None)
            worker = self._main.get(camera_id)
            if worker is not None and worker.alive:
                return worker

            s = get_settings()
            worker = CameraWorker(
                camera_id, main_url, label=f"cam{camera_id}/고화질",
                reconnect_sec=s.rtsp_reconnect_sec,
                read_timeout_sec=s.rtsp_read_timeout_sec,
                ffmpeg_options=s.rtsp_ffmpeg_options,
                jpeg_quality=s.jpeg_quality,
                max_width=s.stream_max_width,
                target_fps=s.stream_fps,
            )
            worker.start()
            self._main[camera_id] = worker
            log.info("고화질 워커 기동: 카메라 %d", camera_id)
            return worker

    async def release_main(self, camera_id: int) -> None:
        """자리를 놓는다. 마지막이면 곧바로 내리지 않고 유예를 준다."""
        async with self._lock:
            left = self._main_holders.get(camera_id, 0) - 1
            if left > 0:
                self._main_holders[camera_id] = left
                return
            self._main_holders.pop(camera_id, None)
            if camera_id in self._main:
                self._main_idle_at[camera_id] = time.monotonic() + MAIN_IDLE_SEC

    async def reap_main(self) -> None:
        """유예가 끝난 고화질 워커를 내린다. 감시 루프가 주기적으로 부른다.

        내리는 일을 acquire/release 안에서 타이머로 하지 않는 이유: 타이머를 들고 있으면
        서버가 내려갈 때 붙잡고 있을 것이 늘어난다. 이미 도는 루프에 얹는 편이 단순하다.
        """
        now = time.monotonic()
        async with self._lock:
            due = [cid for cid, at in self._main_idle_at.items()
                   if at <= now and not self._main_holders.get(cid)]
            for cid in due:
                worker = self._main.get(cid)
                self._main_idle_at.pop(cid, None)
                if worker is None:
                    continue
                await asyncio.to_thread(worker.stop)
                if worker.alive:
                    # 아직 read() 안에 갇혀 있다. 다음 바퀴에 다시 본다 — 자리를
                    # 비우면 그 사이에 새 워커가 떠서 같은 스트림을 둘이 문다.
                    self._main_idle_at[cid] = now + 5.0
                    continue
                self._main.pop(cid, None)
                log.info("고화질 워커 정지: 카메라 %d", cid)

    async def sync(self, session: AsyncSession) -> None:
        """DB 상태에 맞춰 미디어 경로와 워커를 기동/정지/재기동한다."""
        from ..media import backend as media_backend

        s = get_settings()
        media = media_backend()
        rows = (await session.execute(select(Camera))).scalars().all()
        labels = {c.id: f"{c.name}({c.location})" for c in rows}

        wanted: dict[int, str] = {}
        urls: dict[int, tuple[str, str]] = {}
        for cam in rows:
            if not cam.enabled:
                continue
            main_url, sub_url = await self._source_for(media, cam)
            urls[cam.id] = (main_url, sub_url)
            # 상시 워커는 저화질로 돈다. 이것이 썸네일·상태 판정·모듈 프레임을 먹인다.
            # 저화질이 없는 카메라는 예전처럼 원본으로 돈다 — 없는 것을 쓸 수는 없다.
            wanted[cam.id] = sub_url or main_url

        # 더는 쓰지 않는 카메라는 미디어 경로에서도 내린다.
        gone = set(self._sources) - set(wanted)
        for cid in gone:
            with contextlib.suppress(Exception):
                await media.drop_stream(cid)

        async with self._lock:
            self._urls = urls
            # 사라졌거나 접속 정보가 바뀐 워커 정리.
            #
            # **끝나지 않은 워커는 목록에 남긴다.** 예전에는 pop 으로 먼저 빼고 stop 을
            # 불러, 스레드가 아직 살아 있어도 다음 줄에서 새 워커를 띄웠다. 같은 스트림을
            # 두 디코더가 빨아들이면 서로 밀려 또 타임아웃이 나고 또 하나가 는다 —
            # 6일 만에 카메라 6대에 디코더가 16개까지 늘어난 경로가 이것이다.
            for cid in list(self._workers):
                if cid in wanted and self._sources.get(cid) == wanted[cid]:
                    continue
                worker = self._workers[cid]
                log.info("카메라 워커 정지: %s", labels.get(cid, cid))
                await asyncio.to_thread(worker.stop)
                if worker.alive:
                    # 아직 read() 안에 갇혀 있다. 소켓 타임아웃에 걸려 몇 초 안에
                    # 빠져나오므로 다음 sync 에서 다시 본다. 그때까지 이 자리를
                    # 비우지 않는다 — 비우면 중복이 생긴다.
                    continue
                self._workers.pop(cid, None)
                self._sources.pop(cid, None)

            # 새로 필요한 워커 기동
            for cid, url in wanted.items():
                if cid in self._workers:
                    # 정지 중이라 아직 남아 있는 자리도 여기에 걸린다. 다음 바퀴에
                    # 실제로 끝나면 그때 새로 띄운다.
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

    async def _source_for(self, media, cam: Camera) -> tuple[str, str]:
        """워커가 붙을 (원본, 저화질) 주소.

        미디어 서버가 살아 있으면 그쪽, 아니면 카메라 직접. 저화질은 카메라에 경로가
        설정돼 있을 때만 나온다 — 없으면 빈 문자열이고, 부르는 쪽이 원본으로 내려간다.
        '없다' 와 '원본과 같다' 를 섞으면 저화질인 줄 알고 원본을 디코딩하게 된다.
        """
        try:
            info = await media.ensure_stream(cam)
        except Exception:                                    # noqa: BLE001
            log.warning("미디어 경로 등록 실패(#%d) — 카메라에 직접 붙습니다", cam.id,
                        exc_info=True)
            return rtsp_url(cam), rtsp_url(cam, sub=True)
        if info.rtsp:
            self._stream_info[cam.id] = info
            return info.rtsp, getattr(info, "rtsp_sub", "") or ""
        log.warning("미디어 스트림 주소를 얻지 못했습니다(#%d): %s — 카메라 직접 접속",
                    cam.id, info.detail)
        return rtsp_url(cam), rtsp_url(cam, sub=True)

    def stream_info(self, camera_id: int):
        """마지막으로 확인된 스트림 접근 정보(분석 모듈에 넘겨 줄 주소)."""
        return self._stream_info.get(camera_id)

    async def shutdown(self) -> None:
        async with self._lock:
            for cid, worker in list(self._main.items()):
                await asyncio.to_thread(worker.stop)
                self._main.pop(cid, None)
            for cid, worker in list(self._workers.items()):
                await asyncio.to_thread(worker.stop)
                self._workers.pop(cid, None)
            self._main_holders.clear()
            self._main_idle_at.clear()
            self._sources.clear()


manager = StreamManager()

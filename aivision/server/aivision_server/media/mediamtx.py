"""MediaMTX 백엔드 — 카메라에서 한 번만 당겨 여러 소비자에게 나눠 준다.

카메라 펌웨어는 동시 RTSP 세션을 보통 5~10 개로 제한하고, 인코딩·전송을 약한 CPU 가 감당한다.
대시보드 2명 + 분석 모듈 2개 + 녹화면 이미 한계다. 그래서 카메라에는 **하나만** 붙고
나머지는 전부 미디어 서버에서 가져간다(fan-out).

경로는 Control API 로 런타임에 등록한다. 설정 파일을 고쳐 재시작하는 방식이 아니라,
어드민에서 카메라를 추가하면 즉시 반영된다.

  경로 이름   cam/{camera_id}          안정적 식별자. 카메라 IP 가 바뀌어도 그대로다.
  Control API :9997                    경로 추가/삭제/조회
  Playback    :9996                    녹화 구간 목록·추출
  RTSP        :8554                    분석 모듈이 가져가는 곳
  WebRTC/HLS  :8889                    브라우저용
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..config import get_settings
from ..timeutil import as_utc
from .backend import BackendHealth, MediaBackend, Segment, StreamInfo

log = logging.getLogger(__name__)

TIMEOUT = 6.0


def path_name(camera_id: int) -> str:
    return f"cam/{camera_id}"


class MediaMTXBackend(MediaBackend):
    name = "mediamtx"
    supports_recording = True

    def __init__(self) -> None:
        s = get_settings()
        self._api = s.mediamtx_api_url.rstrip("/")
        self._playback = s.mediamtx_playback_url.rstrip("/")
        self._rtsp_host = s.mediamtx_rtsp_host
        self._public = s.mediamtx_public_url.rstrip("/")
        self._record_dir = s.mediamtx_record_dir
        self._client = httpx.AsyncClient(timeout=TIMEOUT)

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------- 내부 호출

    async def _call(self, method: str, path: str, **kw) -> tuple[bool, Any]:
        """Control API 호출. 미디어 서버가 죽어 있어도 코어는 계속 돌아야 하므로
        예외를 밖으로 던지지 않고 (성공여부, 본문) 으로 돌려준다."""
        url = f"{self._api}{path}"
        try:
            r = await self._client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            log.debug("MediaMTX 호출 실패 %s %s: %s", method, path, exc)
            return False, str(exc)
        if r.status_code >= 400:
            body = r.text[:200]
            log.debug("MediaMTX %s %s -> %d %s", method, path, r.status_code, body)
            return False, body
        if not r.content:
            return True, None
        try:
            return True, r.json()
        except ValueError:
            return True, r.text

    # ------------------------------------------------------------- 상태

    async def health(self) -> BackendHealth:
        ok, body = await self._call("GET", "/v3/paths/list")
        if not ok:
            return BackendHealth(self.name, False, f"Control API 응답 없음 ({self._api}): {body}")
        items = body.get("items", []) if isinstance(body, dict) else []
        ready = sum(1 for p in items if p.get("ready"))
        return BackendHealth(self.name, True, f"경로 {len(items)}개 · 수신 중 {ready}개",
                             streams=len(items),
                             extra={"api": self._api, "rtsp": self._rtsp_host})

    # ------------------------------------------------------------- 스트림

    async def ensure_stream(self, camera) -> StreamInfo:
        from ..streaming.manager import rtsp_url

        name = path_name(camera.id)
        conf = {
            "source": rtsp_url(camera),
            "sourceProtocol": "tcp",
            # on-demand 로 두면 보는 사람이 없을 때 카메라 연결이 끊긴다.
            # 녹화와 상시 감시를 하려면 항상 붙어 있어야 한다.
            "sourceOnDemand": False,
        }
        ok, body = await self._call("POST", f"/v3/config/paths/add/{name}", json=conf)
        if not ok:
            # 이미 있으면 patch 로 갱신한다(접속 정보가 바뀌었을 수 있다).
            ok, body = await self._call("PATCH", f"/v3/config/paths/patch/{name}", json=conf)
        if not ok:
            return StreamInfo(stream_id=name, detail=f"경로 등록 실패: {body}")

        info = await self.stream_info(camera.id)
        return info or self._urls(name)

    async def drop_stream(self, camera_id: int) -> None:
        name = path_name(camera_id)
        await self._call("DELETE", f"/v3/config/paths/delete/{name}")

    async def stream_info(self, camera_id: int) -> StreamInfo | None:
        name = path_name(camera_id)
        ok, body = await self._call("GET", f"/v3/paths/get/{name}")
        info = self._urls(name)
        if not ok or not isinstance(body, dict):
            info.detail = "경로 없음 또는 미디어 서버 응답 없음"
            return info
        info.ready = bool(body.get("ready"))
        info.detail = "" if info.ready else "카메라에서 영상을 받지 못하고 있습니다"
        return info

    def _urls(self, name: str) -> StreamInfo:
        return StreamInfo(
            stream_id=name,
            rtsp=f"rtsp://{self._rtsp_host}/{name}",
            webrtc=f"{self._public}/{name}",
            hls=f"{self._public}/{name}/index.m3u8",
        )

    # ------------------------------------------------------------- 녹화

    async def set_recording(self, camera_id: int, enabled: bool,
                            retention_hours: int = 72) -> bool:
        name = path_name(camera_id)
        conf = {
            "record": enabled,
            # MediaMTX 는 경로별 recordPath 에도 %path 를 요구한다(경로 이름으로 치환된다).
            # 직접 "cam/2" 를 박아 넣으면 "'recordPath' must contain %path" 로 거절당한다.
            "recordPath": f"{self._record_dir}/%path/%Y-%m-%d_%H-%M-%S-%f",
            "recordFormat": "fmp4",
            "recordSegmentDuration": "1h",
            "recordDeleteAfter": f"{max(1, retention_hours)}h",
        }
        ok, _ = await self._call("PATCH", f"/v3/config/paths/patch/{name}", json=conf)
        if ok:
            log.info("녹화 %s: %s (보존 %d시간)", "켬" if enabled else "끔", name, retention_hours)
        return ok

    async def list_segments(self, camera_id: int, start: datetime,
                            end: datetime) -> list[Segment]:
        """Playback 서버에서 세그먼트 목록을 가져온다.

        코어는 이 목록을 DB 에 미러링하지 않는다. 세그먼트는 수만 개로 계속 회전하므로
        진실의 원천을 미디어 서버에 두고 필요할 때 물어보는 편이 낫다.
        """
        name = path_name(camera_id)
        try:
            r = await self._client.get(f"{self._playback}/list", params={"path": name})
            r.raise_for_status()
            items = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.debug("세그먼트 조회 실패 %s: %s", name, exc)
            return []

        out: list[Segment] = []
        lo, hi = as_utc(start), as_utc(end)
        for it in items if isinstance(items, list) else []:
            try:
                seg_start = as_utc(datetime.fromisoformat(str(it["start"]).replace("Z", "+00:00")))
                dur = float(it.get("duration", 0))
            except (KeyError, ValueError, TypeError):
                continue
            if seg_start is None or hi is None or lo is None:
                continue
            if seg_start > hi or seg_start + timedelta(seconds=dur) < lo:
                continue
            out.append(Segment(start=seg_start, duration_sec=dur,
                               url=self.playback_url(camera_id, seg_start, dur)))
        out.sort(key=lambda s: s.start)
        return out

    def playback_url(self, camera_id: int, start: datetime, duration_sec: float) -> str:
        aware = as_utc(start)
        stamp = aware.isoformat().replace("+00:00", "Z") if aware else ""
        return (f"{self._playback}/get?path={path_name(camera_id)}"
                f"&start={stamp}&duration={duration_sec:.0f}&format=mp4")

    async def export_clip(self, camera_id: int, start: datetime, duration_sec: float,
                          out_path: Path) -> Path | None:
        """구간을 내려받아 별도 파일로 저장한다 — 사고 영상은 코어가 소유해야 한다."""
        url = self.playback_url(camera_id, start, duration_sec)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            async with self._client.stream("GET", url, timeout=60.0) as r:
                if r.status_code >= 400:
                    log.warning("클립 추출 실패 %s -> %d", url, r.status_code)
                    return None
                with out_path.open("wb") as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)
        except (httpx.HTTPError, OSError) as exc:
            log.warning("클립 추출 실패(%s): %s", out_path.name, exc)
            return None
        if out_path.stat().st_size == 0:
            out_path.unlink(missing_ok=True)
            return None
        log.info("이벤트 클립 저장: %s (%.0f초, %d bytes)",
                 out_path.name, duration_sec, out_path.stat().st_size)
        return out_path

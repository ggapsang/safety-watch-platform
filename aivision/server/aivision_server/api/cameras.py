"""카메라 API — 목록·인라인 수정(통계/카메라 화면)과 어드민 등록이 같은 경로를 쓴다."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..crypto import encrypt
from ..db import get_session
from ..detection.registry import registry
from ..models import Camera, CameraSolution, Event, Solution
from ..schemas import (CameraCreate, CameraOrder, CameraOut, CameraPatch,
                       CameraTestResult)
from ..services.bus import bus
from ..streaming.manager import manager, masked_rtsp_url, rtsp_url
from ..timeutil import age_sec, as_utc

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cameras", tags=["cameras"])


# ────────────────────────────────────────────────────────────── DTO

def to_dto(cam: Camera, *, today: int = 0, total: int = 0) -> CameraOut:
    worker = manager.get(cam.id)
    # '정상'의 기준: 영상이 들어오거나(워커 연결) MQTT heartbeat 가 최근에 있었거나.
    # 둘 다 없으면 오프라인이다. 한쪽만 죽어도 운영자는 알아야 하므로 last_error 를 함께 준다.
    online = bool(worker and worker.connected) or _recently_seen(cam)
    return CameraOut(
        id=cam.id, name=cam.name, location=cam.location, note=cam.note,
        ip=cam.ip, rtsp_port=cam.rtsp_port, rtsp_path=cam.rtsp_path,
        username=cam.username, has_password=bool(cam.password_enc),
        mac=cam.mac, vendor=cam.vendor, model=cam.model,
        enabled=cam.enabled,
        status="normal" if (cam.enabled and online) else "offline",
        last_seen_at=as_utc(cam.last_seen_at),
        last_error=(worker.last_error if worker else cam.last_error),
        sols=[cs.solution_code for cs in cam.solutions if cs.enabled],
        detection_source="HANWHA" if cam.vendor.upper() == "HANWHA" else cam.vendor.upper(),
        stream_url=f"/api/stream/{cam.id}",
        rtsp_url=getattr(manager.stream_info(cam.id), "rtsp", "") or "",
        record_enabled=cam.record_enabled,
        record_retention_hours=cam.record_retention_hours,
        sort_order=cam.sort_order,
        today=today, total=total,
    )


def _recently_seen(cam: Camera) -> bool:
    """MQTT heartbeat 가 최근에 있었는지. (영상이 끊겨도 장비 자체는 살아 있을 수 있다.)"""
    return age_sec(cam.last_seen_at) < get_settings().heartbeat_stale_sec


async def _event_counts(session: AsyncSession) -> tuple[dict[int, int], dict[int, int]]:
    from ..services.stats import local_tz

    midnight = datetime.now(local_tz()).replace(hour=0, minute=0, second=0, microsecond=0)
    total_rows = (await session.execute(
        select(Event.camera_id, func.count(Event.id)).group_by(Event.camera_id))).all()
    today_rows = (await session.execute(
        select(Event.camera_id, func.count(Event.id))
        .where(Event.ts >= midnight).group_by(Event.camera_id))).all()
    return ({cid: int(n) for cid, n in today_rows}, {cid: int(n) for cid, n in total_rows})


async def _load(session: AsyncSession, camera_id: int) -> Camera | None:
    """관계까지 즉시 적재된 카메라를 읽는다(비동기 세션에서 지연 로딩을 피하기 위해)."""
    return (await session.execute(
        select(Camera).where(Camera.id == camera_id))).scalars().unique().first()


async def _get(session: AsyncSession, camera_id: int) -> Camera:
    cam = await _load(session, camera_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="카메라를 찾을 수 없습니다")
    return cam


async def _apply_solutions(session: AsyncSession, cam: Camera, codes: list[str]) -> None:
    """솔루션 매핑을 통째로 교체한다.

    주의: cam.solutions 컬렉션을 직접 건드리지 않는다. 갓 flush 한 카메라는 컬렉션이 아직
      적재되지 않아 .clear() 가 지연 로딩을 유발하고, 비동기 세션에서는 그것이 예외가 된다.
      행을 직접 지우고 다시 넣는 편이 안전하고 쿼리도 적다.
    """
    valid = {c for c in (await session.execute(select(Solution.code))).scalars()}
    unknown = [c for c in codes if c not in valid]
    if unknown:
        raise HTTPException(status_code=400, detail=f"없는 솔루션 코드: {', '.join(unknown)}")
    await session.execute(delete(CameraSolution).where(CameraSolution.camera_id == cam.id))
    for code in dict.fromkeys(codes):          # 중복 제거, 순서 유지
        session.add(CameraSolution(camera_id=cam.id, solution_code=code, enabled=True))
    await session.flush()


async def _after_change(session: AsyncSession) -> None:
    """카메라가 바뀌면 미디어 경로·워커·바인딩 캐시를 즉시 맞춘다."""
    from ..services.recording import apply_all

    await manager.sync(session)
    await apply_all(session)
    await registry.reload_all()
    await bus.publish("cameras-changed", {})


# ────────────────────────────────────────────────────────────── 라우트

@router.get("", response_model=list[CameraOut])
async def list_cameras(session: AsyncSession = Depends(get_session)) -> list[CameraOut]:
    cams = (await session.execute(
        select(Camera).order_by(Camera.sort_order, Camera.id))).scalars().unique().all()
    today, total = await _event_counts(session)
    return [to_dto(c, today=today.get(c.id, 0), total=total.get(c.id, 0)) for c in cams]


@router.put("/order", response_model=list[CameraOut])
async def reorder_cameras(body: CameraOrder,
                          session: AsyncSession = Depends(get_session)) -> list[CameraOut]:
    """카메라를 보고 싶은 순서로 다시 늘어놓는다.

    받은 목록에 없는 카메라는 뒤로 밀되 서로의 순서는 유지한다. 화면이 필터를 걸고 있어
    일부만 보내는 경우가 있는데, 그때 안 보이던 카메라가 맨 앞으로 튀어나오면 곤란하다.
    """
    cams = (await session.execute(
        select(Camera).order_by(Camera.sort_order, Camera.id))).scalars().unique().all()
    by_id = {c.id: c for c in cams}

    unknown = [i for i in body.ids if i not in by_id]
    if unknown:
        raise HTTPException(status_code=400, detail=f"없는 카메라입니다: {unknown}")

    order = 0
    for cam_id in body.ids:
        by_id[cam_id].sort_order = order
        order += 1
    for cam in cams:                      # 목록에 없던 것들은 뒤에 이어 붙인다
        if cam.id not in body.ids:
            cam.sort_order = order
            order += 1

    await session.commit()
    log.info("카메라 순서 변경: %s", " > ".join(str(i) for i in body.ids))
    await bus.publish("cameras-changed", {})

    today, total = await _event_counts(session)
    fresh = (await session.execute(
        select(Camera).order_by(Camera.sort_order, Camera.id))).scalars().unique().all()
    return [to_dto(c, today=today.get(c.id, 0), total=total.get(c.id, 0)) for c in fresh]


@router.post("", response_model=CameraOut, status_code=201)
async def create_camera(body: CameraCreate,
                        session: AsyncSession = Depends(get_session)) -> CameraOut:
    ip = body.ip.strip()
    # 이름·설치 위치를 비워 두면 IP 로 채운다. 목록에서 식별만 되면 되고, 이름은 나중에 붙인다.
    cam = Camera(
        name=body.name.strip() or ip, location=body.location.strip() or ip,
        note=body.note.strip(),
        ip=ip, rtsp_port=body.rtsp_port, rtsp_path=body.rtsp_path.strip(),
        username=body.username.strip(), password_enc=encrypt(body.password),
        mac=body.mac, vendor=body.vendor.strip().upper(), model=body.model.strip(),
        enabled=body.enabled,
        record_enabled=body.record_enabled,
        record_retention_hours=max(1, body.record_retention_hours),
    )
    session.add(cam)
    try:
        await session.flush()
        await _apply_solutions(session, cam, body.sols)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409,
                            detail="같은 IP·경로의 카메라가 이미 등록되어 있습니다") from None
    cam = await _get(session, cam.id)
    await _after_change(session)
    log.info("카메라 등록: #%d %s (%s)", cam.id, cam.name, masked_rtsp_url(cam))
    return to_dto(cam)


@router.patch("/{camera_id}", response_model=CameraOut)
async def patch_camera(camera_id: int, body: CameraPatch,
                       session: AsyncSession = Depends(get_session)) -> CameraOut:
    cam = await _get(session, camera_id)
    data = body.model_dump(exclude_unset=True)

    # 화면에서 이름·위치를 빈 값으로 저장하는 사고를 막는다(둘 다 목록의 식별자 역할이다).
    for field in ("name", "location"):
        if field in data:
            value = (data[field] or "").strip()
            if not value:
                raise HTTPException(status_code=400,
                                    detail="이름과 설치 위치는 비울 수 없습니다")
            data[field] = value

    sols = data.pop("sols", None)
    password = data.pop("password", None)
    for key, value in data.items():
        setattr(cam, key, value.strip() if isinstance(value, str) else value)
    if password:                                # 빈 문자열이면 '변경 없음'
        cam.password_enc = encrypt(password)
    if sols is not None:
        await _apply_solutions(session, cam, sols)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409,
                            detail="같은 IP·경로의 카메라가 이미 등록되어 있습니다") from None
    cam = await _get(session, cam.id)
    await _after_change(session)
    return to_dto(cam)


@router.delete("/{camera_id}", status_code=204)
async def delete_camera(camera_id: int, session: AsyncSession = Depends(get_session)) -> None:
    cam = await _get(session, camera_id)
    # 주의: 이벤트도 함께 지워진다(FK CASCADE). 이력을 남기려면 삭제 대신 enabled=false 를 쓴다.
    await session.delete(cam)
    await session.commit()
    await _after_change(session)
    log.info("카메라 삭제: #%d", camera_id)


@router.post("/{camera_id}/test", response_model=CameraTestResult)
async def test_camera(camera_id: int,
                      session: AsyncSession = Depends(get_session)) -> CameraTestResult:
    """등록된 카메라의 RTSP 연결을 실제로 열어 본다(어드민 '연결 테스트')."""
    cam = await _get(session, camera_id)
    return await _probe(rtsp_url(cam), masked_rtsp_url(cam))


@router.post("/test", response_model=CameraTestResult)
async def test_endpoint(body: CameraCreate) -> CameraTestResult:
    """아직 등록하지 않은 접속 정보를 시험한다(등록 폼의 '연결 테스트')."""
    from urllib.parse import quote

    cred = f"{quote(body.username, safe='')}:{quote(body.password, safe='')}@" if body.username else ""
    port = body.rtsp_port or 554
    host = f"{body.ip}:{port}" if port != 554 else body.ip
    path = body.rtsp_path if body.rtsp_path.startswith("/") else "/" + body.rtsp_path
    masked = f"rtsp://{body.username}:****@{host}{path}" if body.username else f"rtsp://{host}{path}"
    return await _probe(f"rtsp://{cred}{host}{path}", masked)


async def _probe(url: str, masked: str, timeout: float = 8.0) -> CameraTestResult:
    """RTSP 를 열어 첫 프레임을 받아 본다. 블로킹이라 스레드로 넘긴다."""

    def work() -> CameraTestResult:
        import cv2

        s = get_settings()
        import os
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", s.rtsp_ffmpeg_options)
        t0 = time.perf_counter()
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        try:
            if not cap.isOpened():
                return CameraTestResult(
                    ok=False, rtsp_ok=False,
                    detail=f"RTSP 접속 실패 — 주소·계정·방화벽을 확인하세요 ({masked})",
                    elapsed_ms=(time.perf_counter() - t0) * 1000)
            ok, frame = cap.read()
            elapsed = (time.perf_counter() - t0) * 1000
            if not ok or frame is None:
                return CameraTestResult(ok=False, rtsp_ok=True,
                                        detail="접속은 되었으나 프레임을 받지 못했습니다 "
                                               "(프로파일 경로를 확인하세요)",
                                        elapsed_ms=elapsed)
            h, w = frame.shape[:2]
            return CameraTestResult(ok=True, rtsp_ok=True,
                                    detail=f"정상 — {w}x{h} 프레임 수신",
                                    width=w, height=h, elapsed_ms=elapsed)
        finally:
            cap.release()

    try:
        return await asyncio.wait_for(asyncio.to_thread(work), timeout=timeout)
    except asyncio.TimeoutError:
        return CameraTestResult(ok=False, rtsp_ok=False,
                                detail=f"응답 시간 초과({timeout:.0f}초) — {masked}")


@router.get("/{camera_id}/status")
async def camera_status(camera_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    cam = await _get(session, camera_id)
    worker = manager.get(cam.id)
    return {
        "camera_id": cam.id,
        "enabled": cam.enabled,
        "rtsp": masked_rtsp_url(cam),
        "last_seen_at": as_utc(cam.last_seen_at),
        "stream": worker.status() if worker else None,
    }

"""녹화 정책과 이벤트 클립 — 코어의 몫.

책임 분리
  미디어 서버 : 세그먼트 파일 쓰기 · 회전 삭제 · 구간 재생 스트리밍  (배관)
  코어(여기)  : 무엇을 언제 녹화할지 · 이벤트 클립 추출과 소유 · 타임라인 조회  (정책·지식)

상시 녹화 세그먼트는 DB 에 미러링하지 않는다. 수만 개로 계속 회전하므로 진실의 원천을
미디어 서버에 두고 필요할 때 물어보는 편이 낫다.

반면 이벤트 클립은 코어 자산이다. 반드시 **별도 파일로 복사해** 와서 `recordings` 에 남긴다.
세그먼트에 참조만 걸어 두면 보존기간이 지나 회전 삭제될 때 사고 영상이 같이 사라진다 —
상시 녹화는 며칠, 사고 클립은 길게, 보존 정책이 다르기 때문이다.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import sessionmaker
from ..media import Segment, backend as media_backend
from ..models import Camera, Event, Recording
from ..timeutil import as_utc

log = logging.getLogger(__name__)

GB = 1024 ** 3
# 이 시간 안에 쓰인 파일은 건드리지 않는다. 미디어 서버가 열어 두고 있을 수 있다.
ACTIVE_SEGMENT_SEC = 120.0


# ────────────────────────────────────────────────────────────── 정책 반영

async def apply_policy(session: AsyncSession, camera: Camera) -> bool:
    """카메라의 녹화 설정을 미디어 서버에 반영한다."""
    media = media_backend()
    if not media.supports_recording:
        if camera.record_enabled:
            log.warning("미디어 백엔드 '%s' 는 녹화를 지원하지 않습니다 — 카메라 #%d 녹화 설정 무시",
                        media.name, camera.id)
        return False
    return await media.set_recording(camera.id, camera.record_enabled,
                                     camera.record_retention_hours)


async def apply_all(session: AsyncSession) -> None:
    """모든 카메라의 녹화 정책을 다시 밀어 넣는다(기동 시·설정 변경 시)."""
    cams = (await session.execute(select(Camera))).scalars().unique().all()
    for cam in cams:
        if cam.enabled:
            await apply_policy(session, cam)


# ────────────────────────────────────────────────────────────── 타임라인

async def timeline(camera_id: int, start: datetime, end: datetime) -> list[Segment]:
    """구간 안의 녹화 세그먼트. 미디어 서버에 물어본다(DB 미러링 없음)."""
    return await media_backend().list_segments(camera_id, start, end)


# ────────────────────────────────────────────────────────── 용량 상한

def _segment_files() -> list[Path]:
    """상시 녹화 세그먼트 파일 목록. 오래된 것부터 정렬해 돌려준다.

    파일을 쓰는 것은 미디어 서버지만 용량 판단은 코어의 몫이다(설계 문서의 책임 분리).
    미디어 서버는 시간 기반 회전(recordDeleteAfter)까지만 할 수 있어서, 용량 상한은
    여기서 지켜야 한다.
    """
    root = get_settings().record_path
    files = [p for p in root.rglob("*") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime)
    return files


def usage() -> dict:
    """지금 얼마나 차 있나. 화면과 정리 로직이 같은 값을 본다."""
    s = get_settings()
    files = _segment_files()
    total = sum(p.stat().st_size for p in files)
    limit = _limit_bytes(s.record_max_gb)
    try:
        disk = shutil.disk_usage(s.record_path)
        free = disk.free
    except OSError:
        free = 0
    return {
        "bytes": total,
        "gb": round(total / GB, 2),
        "files": len(files),
        "limit_gb": s.record_max_gb,
        "over": bool(limit and total > limit),
        "disk_free_gb": round(free / GB, 1),
    }


def _limit_bytes(max_gb: float) -> int:
    return int(max_gb * GB) if max_gb and max_gb > 0 else 0


async def enforce_quota(max_gb: float | None = None) -> dict:
    """상한을 넘으면 **오래된 세그먼트부터** 지운다.

    지키는 규칙 둘.
      · 카메라별로 나누지 않는다. 디스크가 하나라서, 카메라마다 상한을 주면 합이 디스크를
        넘을 수 있어 정작 막고 싶었던 사고를 못 막는다.
      · **가장 최근 파일은 건드리지 않는다.** 미디어 서버가 지금 쓰고 있는 세그먼트일 수
        있다. 열려 있는 파일을 지우면 녹화가 깨지거나 공간이 실제로 반환되지 않는다.

    실제 지운 바이트 수를 돌려준다.
    """
    s = get_settings()
    limit = _limit_bytes(s.record_max_gb if max_gb is None else max_gb)
    if not limit:
        return {"deleted": 0, "bytes": 0, "limit_gb": 0}

    files = _segment_files()
    total = sum(p.stat().st_size for p in files)
    if total <= limit:
        return {"deleted": 0, "bytes": 0, "limit_gb": limit / GB}

    freed, removed = 0, 0
    now = time.time()
    # 마지막 한 건은 남긴다(쓰고 있을 수 있다). 최근에 손댄 파일도 건너뛴다.
    for path in files[:-1]:
        if total - freed <= limit:
            break
        try:
            stat = path.stat()
            if now - stat.st_mtime < ACTIVE_SEGMENT_SEC:
                continue
            size = stat.st_size
            path.unlink()
        except OSError as exc:
            log.warning("세그먼트 삭제 실패 %s: %s", path.name, exc)
            continue
        freed += size
        removed += 1
        log.info("용량 초과 정리: %s (%.1fMB)", path.name, size / 1024 / 1024)

    if removed:
        log.info("상시 녹화 정리 완료: %d개 · %.2fGB 확보 (상한 %.1fGB)",
                 removed, freed / GB, limit / GB)
    elif total > limit:
        log.warning("용량이 상한(%.1fGB)을 넘었지만 지울 수 있는 세그먼트가 없습니다 "
                    "— 쓰는 중이거나 파일이 하나뿐입니다", limit / GB)
    return {"deleted": removed, "bytes": freed, "limit_gb": limit / GB}


# ────────────────────────────────────────────────────────────── 이벤트 클립

def schedule_event_clip(event_id: int, camera_id: int, ts: datetime) -> None:
    """이벤트 발생 직후 호출. 뒤쪽 여유분이 녹화될 때까지 기다렸다 잘라 낸다.

    적재 경로를 막지 않으려고 백그라운드로 돌린다. 클립을 못 뽑아도 이벤트는 이미 살아 있다.
    """
    asyncio.create_task(_extract_later(event_id, camera_id, ts),
                        name=f"clip-{event_id}")


async def _extract_later(event_id: int, camera_id: int, ts: datetime) -> None:
    s = get_settings()
    media = media_backend()
    if not media.supports_recording:
        return

    # 사고 '이후'까지 담으려면 그 시간만큼 녹화가 쌓일 때까지 기다려야 한다.
    # 여유를 조금 더 준다 — 미디어 서버가 세그먼트를 flush 하는 시간이 필요하다.
    await asyncio.sleep(s.clip_post_sec + 3.0)

    aware = as_utc(ts)
    if aware is None:
        return
    start = aware - timedelta(seconds=s.clip_pre_sec)
    duration = s.clip_pre_sec + s.clip_post_sec

    async with sessionmaker()() as session:
        event = await session.get(Event, event_id)
        if event is None:            # 그 사이 지워졌을 수 있다
            return
        cam = await session.get(Camera, camera_id)
        if cam is None or not cam.record_enabled:
            return                   # 상시 녹화가 꺼져 있으면 뽑을 원본이 없다

        out = s.clip_path / f"{event.code}.mp4"
        path = await media.export_clip(camera_id, start, duration, out)
        if path is None:
            log.info("이벤트 클립 없음(%s) — 해당 구간 녹화가 없을 수 있습니다", event.code)
            return

        session.add(Recording(
            camera_id=camera_id, event_id=event_id, kind="event",
            start_at=start, duration_sec=duration, path=str(path),
            size_bytes=path.stat().st_size,
            note=f"{event.code} 전후 {s.clip_pre_sec:.0f}/{s.clip_post_sec:.0f}초",
        ))
        await session.commit()
        log.info("이벤트 클립 저장: %s (%.0f초)", event.code, duration)


async def clip_of(session: AsyncSession, event_id: int) -> Recording | None:
    return (await session.execute(
        select(Recording).where(Recording.event_id == event_id, Recording.kind == "event")
        .order_by(Recording.id.desc()).limit(1)
    )).scalars().first()

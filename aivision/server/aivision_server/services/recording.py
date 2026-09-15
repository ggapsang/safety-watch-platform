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
from .stats import local_tz

log = logging.getLogger(__name__)

GB = 1024 ** 3
# 이 시간 안에 쓰인 파일은 건드리지 않는다. 미디어 서버가 열어 두고 있을 수 있다.
ACTIVE_SEGMENT_SEC = 120.0


# ──────────────────────────────────────────────────────── 요일·시간대 스케줄

DAY_NAMES = ("월", "화", "수", "목", "금", "토", "일")


def _minutes(hhmm: str) -> int | None:
    """'08:30' -> 510. 읽을 수 없으면 None."""
    try:
        h, m = str(hhmm).split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h <= 24 and 0 <= m < 60):
        return None
    return h * 60 + m


def windows_of(schedule: dict | None) -> list[dict]:
    """설정에서 쓸 수 있는 구간만 추려 낸다. 잘못 적힌 줄은 조용히 버린다.

    여기서 예외를 올리면 설정 한 줄 때문에 그 카메라의 녹화 판단이 통째로 멈춘다.
    잘못 적힌 구간은 '없는 구간' 으로 보는 편이 안전하다 — 녹화가 안 되는 것은 화면에서
    보이지만, 예외로 죽은 판단은 아무 데도 안 보인다.
    """
    if not isinstance(schedule, dict):
        return []
    out = []
    for w in schedule.get("windows") or []:
        if not isinstance(w, dict):
            continue
        start, end = _minutes(w.get("start")), _minutes(w.get("end"))
        if start is None or end is None or start == end:
            continue
        days = [d for d in (w.get("days") or []) if isinstance(d, int) and 0 <= d <= 6]
        out.append({"days": sorted(set(days)) or list(range(7)), "start": start, "end": end})
    return out


def should_record(schedule: dict | None, now: datetime) -> bool:
    """지금이 녹화할 시간인가. `now` 는 **현장 시간대**의 시각이어야 한다.

    구간이 하나도 없으면 True — 스케줄을 안 쓰는 카메라는 늘 녹화한다.

    `start > end` 는 자정을 넘는 구간이다(22:00~06:00). 이때 '요일' 은 **구간이 시작한
    날**을 가리킨다. 금요일 22:00~06:00 이라고 적었으면 토요일 새벽 2시도 녹화한다 —
    사람이 '금요일 밤샘' 이라고 말할 때 뜻하는 것이 그것이다.
    """
    windows = windows_of(schedule)
    if not windows:
        return True

    minute = now.hour * 60 + now.minute
    today = now.weekday()
    yesterday = (today - 1) % 7
    for w in windows:
        if w["start"] < w["end"]:
            if today in w["days"] and w["start"] <= minute < w["end"]:
                return True
        else:
            # 자정을 넘는다. 오늘 밤 시작분이거나, 어제 시작해 넘어온 새벽분이다.
            if today in w["days"] and minute >= w["start"]:
                return True
            if yesterday in w["days"] and minute < w["end"]:
                return True
    return False


def describe(schedule: dict | None) -> str:
    """사람이 읽을 요약. 화면과 로그가 같은 말을 쓰게 한다."""
    windows = windows_of(schedule)
    if not windows:
        return "항상"
    parts = []
    for w in windows:
        days = ("매일" if len(w["days"]) == 7
                else "".join(DAY_NAMES[d] for d in w["days"]))
        parts.append(f"{days} {w['start'] // 60:02d}:{w['start'] % 60:02d}"
                     f"~{w['end'] // 60:02d}:{w['end'] % 60:02d}")
    return ", ".join(parts)


# ────────────────────────────────────────────────────────────── 정책 반영

# 미디어 서버에 마지막으로 밀어 넣은 상태. 스케줄 때문에 5초마다 판단하지만, 바뀌지
# 않았는데 매번 PATCH 를 보내면 미디어 서버 로그가 그것으로 가득 찬다.
_applied: dict[int, tuple[bool, int]] = {}


def wanted_now(camera: Camera, now: datetime | None = None) -> bool:
    """이 카메라가 지금 녹화해야 하는가. 스위치와 스케줄을 함께 본다.

    `record_enabled` 는 주 스위치다. 꺼져 있으면 스케줄과 무관하게 녹화하지 않는다 —
    스케줄은 '켜 둔 동안 언제' 를 정할 뿐이다.
    """
    if not camera.record_enabled:
        return False
    return should_record(camera.record_schedule, now or datetime.now(local_tz()))


async def apply_policy(session: AsyncSession, camera: Camera, *,
                       force: bool = False) -> bool:
    """카메라의 녹화 설정을 미디어 서버에 반영한다.

    `force` 는 캐시를 무시하고 다시 밀어 넣는다. 미디어 서버가 재시작하면 경로 설정이
    초기화되는데, 우리 캐시는 그것을 모르기 때문이다.
    """
    media = media_backend()
    if not media.supports_recording:
        if camera.record_enabled:
            log.warning("미디어 백엔드 '%s' 는 녹화를 지원하지 않습니다 — 카메라 #%d 녹화 설정 무시",
                        media.name, camera.id)
        return False

    want = wanted_now(camera)
    state = (want, camera.record_retention_hours)
    if not force and _applied.get(camera.id) == state:
        return True

    ok = await media.set_recording(camera.id, want, camera.record_retention_hours)
    if ok:
        _applied[camera.id] = state
    return ok


async def apply_all(session: AsyncSession, *, force: bool = False) -> None:
    """모든 카메라의 녹화 정책을 다시 밀어 넣는다(기동 시·설정 변경 시·스케줄 점검 시)."""
    cams = (await session.execute(select(Camera))).scalars().unique().all()
    for cam in cams:
        if cam.enabled:
            await apply_policy(session, cam, force=force)


def forget(camera_id: int) -> None:
    """캐시에서 뺀다. 카메라를 지우거나 설정을 바꿔 다시 밀어야 할 때."""
    _applied.pop(camera_id, None)


# ────────────────────────────────────────────────────────────── 타임라인

async def timeline(camera_id: int, start: datetime, end: datetime) -> list[Segment]:
    """구간 안의 녹화 세그먼트. 미디어 서버에 물어본다(DB 미러링 없음)."""
    return await media_backend().list_segments(camera_id, start, end)


# ────────────────────────────────────────────────────────── 용량 상한

def _segment_files(camera_id: int | None = None) -> list[Path]:
    """상시 녹화 세그먼트 파일 목록. 오래된 것부터 정렬해 돌려준다.

    파일을 쓰는 것은 미디어 서버지만 용량 판단은 코어의 몫이다(설계 문서의 책임 분리).
    미디어 서버는 시간 기반 회전(recordDeleteAfter)까지만 할 수 있어서, 용량 상한은
    여기서 지켜야 한다.

    `camera_id` 를 주면 그 카메라 몫만 본다. 미디어 서버가 경로 이름(`cam/{id}`)으로
    폴더를 나눠 쓰므로 그대로 따라간다 — 파일 이름을 파싱하지 않는다.
    """
    root = get_settings().record_path
    if camera_id is not None:
        root = root / "cam" / str(camera_id)
        if not root.is_dir():
            return []
    files = [p for p in root.rglob("*") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime)
    return files


def _trim(files: list[Path], limit: int, label: str) -> tuple[int, int]:
    """오래된 것부터 지워 `limit` 아래로 내린다. (지운 개수, 확보한 바이트).

    **가장 최근 파일은 건드리지 않는다.** 미디어 서버가 지금 쓰고 있는 세그먼트일 수
    있고, 열려 있는 파일을 지우면 녹화가 깨지거나 공간이 실제로 반환되지 않는다.
    """
    total = sum(p.stat().st_size for p in files)
    if not limit or total <= limit:
        return 0, 0

    freed, removed = 0, 0
    now = time.time()
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

    if removed:
        log.info("%s 정리: %d개 · %.2fGB 확보 (상한 %.1fGB)",
                 label, removed, freed / GB, limit / GB)
    elif total > limit:
        log.warning("%s 가 상한(%.1fGB)을 넘었지만 지울 수 있는 세그먼트가 없습니다 "
                    "— 쓰는 중이거나 파일이 하나뿐입니다", label, limit / GB)
    return removed, freed


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
        # '지금 어디에 쌓이나'. 도커 볼륨 이름이거나 호스트 폴더 경로다.
        "location": s.record_location or str(s.record_dir),
    }


def camera_usage(camera_id: int) -> dict:
    """카메라 한 대가 쓰고 있는 용량. 카메라별 상한과 짝이 되는 값이다."""
    files = _segment_files(camera_id)
    total = sum(p.stat().st_size for p in files)
    return {"bytes": total, "gb": round(total / GB, 2), "files": len(files)}


def _limit_bytes(max_gb: float) -> int:
    return int(max_gb * GB) if max_gb and max_gb > 0 else 0


async def enforce_quota(max_gb: float | None = None) -> dict:
    """상한을 넘으면 **오래된 세그먼트부터** 지운다. 두 층으로 지킨다.

      1. **카메라별 상한** — 한 대가 디스크를 독차지하는 것을 막는다. 종일 사람이
         지나다니는 출입구 카메라 하나가 나머지의 어제 영상을 밀어내는 일이 실제로 있다.
      2. **전체 상한** — 마지막 방어선. 카메라별 상한만 두면 그 합이 디스크를 넘을 수
         있어 정작 막고 싶었던 사고를 못 막는다. 그래서 전체 상한을 없애지 않는다.

    순서가 중요하다. 카메라별로 먼저 깎으면 전체 정리가 할 일이 줄고, 남는 것도 카메라
    사이에 고르게 남는다. 전체부터 깎으면 가장 오래된 카메라의 영상만 통째로 사라진다.
    """
    s = get_settings()
    per_camera: list[dict] = []
    removed = freed = 0

    # 1층 — 카메라별. DB 를 읽어야 하므로 여기서 연다(용량 정리는 5분에 한 번이다).
    async with sessionmaker()() as session:
        cams = (await session.execute(select(Camera))).scalars().unique().all()
        limits = [(c.id, c.name, _limit_bytes(c.record_max_gb)) for c in cams]

    for cam_id, name, limit in limits:
        if not limit:
            continue
        n, b = _trim(_segment_files(cam_id), limit, f"카메라 {name} 녹화")
        if n:
            per_camera.append({"camera_id": cam_id, "deleted": n, "bytes": b})
            removed += n
            freed += b

    # 2층 — 전체.
    total_limit = _limit_bytes(s.record_max_gb if max_gb is None else max_gb)
    if total_limit:
        n, b = _trim(_segment_files(), total_limit, "상시 녹화 전체")
        removed += n
        freed += b

    return {"deleted": removed, "bytes": freed,
            "limit_gb": total_limit / GB if total_limit else 0,
            "per_camera": per_camera}


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

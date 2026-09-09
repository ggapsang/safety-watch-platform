"""설정 파일 — 커밋할 수 있는 것과 커밋할 수 없는 것을 가른다.

**문제.** 바인딩·탐지 항목·운영 설정을 DB 행으로만 들고 있었다. DB 는 이벤트와
원문 로그가 계속 쌓여 수 GB 로 커지는 물건이라 **저장소에 올릴 수 없다.** 그러면
설정도 같이 못 올린다 — 커질수록 꺼낼 방법이 없어진다. 현장에서 화면으로 맞춰 둔
배선이 그 PC 안에만 남고, 이관하려면 손으로 다시 만들어야 한다.

**가르는 기준.** 사람이 정한 것인가, 기계가 쌓은 것인가.

    설정(사람) — 탐지 항목 · 인바운드 바인딩 · 운영 설정
                  파일이 원본. 저장소에 커밋한다. 몇 KB 이고 트래픽과 무관하다.
    데이터(기계) — 이벤트 · 박스 · 원문 로그 · 녹화 · 카메라 접속정보
                  DB 가 원본. 커밋하지 않는다. GB 로 커지는 것은 전부 이쪽이다.

**DB 에 여전히 두는 이유.** 화면이 바인딩을 실시간으로 고칠 수 있어야 하고
(매니페스토 2번 — 코어는 무엇을 탐지하는지 모르고, 어드민이 토픽만 알면 코드 없이
받아들인다), 조회할 때마다 파일을 읽을 수는 없다. 그래서 **파일이 원본, DB 는 사본**
이다. 기동할 때 파일 -> DB 로 붓고(`apply`), 화면에서 고친 뒤에는 DB -> 파일로
되뽑아(`export`) 커밋한다.

**붓는 규칙은 '없는 것만'.** 파일에 있고 DB 에 없으면 만든다. 이미 있으면 손대지
않는다 — 운영 중에 화면에서 고친 값을 기동할 때마다 되돌리면 안 된다. 파일 쪽이
맞다고 확신할 때는 화면에서 지우고 다시 올리면 된다.

카메라 자체(IP·비밀번호)는 여기 넣지 않는다. 현장마다 다르고 저장소에 들어가서는 안 된다.
다만 `camera_from=fixed` 바인딩이 가리키는 `camera_id` 는 담는다 — 없으면 그 바인딩을
파일로 온전히 표현할 수 없다. 환경을 옮기면 그 번호가 안 맞을 수 있는데, 없는 번호면
실패시키지 않고 비운 채 꺼 둔다. 화면에서 카메라를 고르고 켜면 된다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Camera, InboundBinding, Solution
from . import settings_store as store

log = logging.getLogger(__name__)

VERSION = 1

# 파일에 담는 컬럼. 여기 없는 것(진단용 last_matched_at·match_count, 자동 증가 id)은
# 기계가 쌓는 것이라 뺀다. id 를 빼는 이유는 환경마다 다르기 때문이다 — 이름으로 찾는다.
_BINDING_FIELDS = (
    "name", "enabled", "transport", "payload_profile", "priority", "live_only",
    "topic_pattern", "payload_filter",
    "camera_from", "camera_expr", "camera_id",
    "item_from", "item_expr", "solution_code",
    "state_expr", "state_active", "state_inactive",
    "module_expr", "confidence_expr", "ts_expr", "boxes_expr", "boxes_format",
)
_SOLUTION_FIELDS = ("code", "name", "short_name", "description", "event_type",
                    "color", "sort_order", "enabled")


def _row(obj: Any, fields: tuple[str, ...]) -> dict:
    return {f: getattr(obj, f) for f in fields}


# ────────────────────────────────────────────────────────────── 읽기

def load(path: Path) -> dict:
    """설정 파일을 읽는다. 없으면 빈 설정. 깨져 있으면 기동을 막지 않고 경고만 낸다."""
    if not path.is_file():
        log.info("설정 파일이 없습니다 (%s) — 배선 없이 기동합니다", path)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # 설정 파일 하나 때문에 서버가 못 뜨면 현장에서 손을 쓸 수 없다.
        log.error("설정 파일을 읽지 못했습니다 (%s): %s — 배선 없이 기동합니다", path, exc)
        return {}
    if not isinstance(data, dict):
        log.error("설정 파일의 최상위가 객체가 아닙니다 (%s) — 무시합니다", path)
        return {}
    return data


async def apply(session: AsyncSession, path: Path) -> list[str]:
    """파일 -> DB. 없는 것만 만든다. 만든 것들의 설명을 돌려준다."""
    data = load(path)
    if not data:
        return []
    made: list[str] = []

    for spec in data.get("solutions") or []:
        code = str(spec.get("code") or "").strip()
        if not code or await session.get(Solution, code) is not None:
            continue
        session.add(Solution(**{k: v for k, v in spec.items() if k in _SOLUTION_FIELDS}))
        made.append(f"탐지 항목 {code}")

    # camera_from=fixed 는 카메라를 못 박아야 하는데, 카메라 id 는 환경마다 다르므로
    # 파일에 담을 수 없다. 카메라가 하나뿐이면 거기에 붙이고, 여럿이면 꺼 둔 채 만든다.
    cams = (await session.execute(select(Camera.id).order_by(Camera.id))).scalars().all()
    only_cam = cams[0] if len(cams) == 1 else None

    for spec in data.get("bindings") or []:
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        if await session.scalar(select(InboundBinding).where(InboundBinding.name == name)):
            continue
        row = {k: v for k, v in spec.items() if k in _BINDING_FIELDS}
        note = ""
        if row.get("camera_from") == "fixed":
            wanted = row.get("camera_id")
            if wanted not in cams:               # 없는 번호이거나 비어 있음
                row["camera_id"] = only_cam
                if only_cam is None:
                    row["enabled"] = False
                    note = " (카메라 지정 필요 — 꺼 둠)"
        session.add(InboundBinding(**row))
        made.append(f"바인딩 '{name}'{note}")

    runtime = data.get("runtime") or {}
    if runtime:
        current = await store.get_runtime(session)
        fresh = {k: v for k, v in runtime.items() if k not in current}
        if fresh:
            await store.set_value(session, store.KEY_RUNTIME, {**current, **fresh})
            made.append(f"운영 설정 {len(fresh)}개")

    if made:
        await session.commit()
    return made


# ────────────────────────────────────────────────────────────── 쓰기

async def export(session: AsyncSession) -> dict:
    """DB -> 파일 내용. 화면에서 고친 배선을 저장소로 되가져오기 위한 것이다."""
    sols = (await session.execute(
        select(Solution).order_by(Solution.sort_order, Solution.code))).scalars().all()
    binds = (await session.execute(
        select(InboundBinding).order_by(InboundBinding.priority,
                                        InboundBinding.id))).scalars().all()
    return {
        "version": VERSION,
        "solutions": [_row(s, _SOLUTION_FIELDS) for s in sols],
        "bindings": [_row(b, _BINDING_FIELDS) for b in binds],
        "runtime": await store.get_runtime(session),
    }


async def save(session: AsyncSession, path: Path) -> dict:
    """현재 DB 설정을 파일에 쓴다. 이 파일을 커밋하면 된다."""
    data = await export(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 들여쓰기와 개행을 고정한다 — diff 가 읽히지 않으면 커밋해도 소용이 없다.
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)                      # 쓰다 죽어도 반쯤 쓰인 파일이 남지 않는다
    log.info("설정을 %s 에 저장했습니다 (항목 %d · 바인딩 %d)",
             path, len(data["solutions"]), len(data["bindings"]))
    return data


# ────────────────────────────────────────────────────────────── 통째로 바꾸기

class ConfigError(Exception):
    """설정을 받아들일 수 없다. 사람이 읽을 이유를 담는다."""


async def replace(session: AsyncSession, data: dict,
                  validate: Any | None = None) -> dict:
    """편집한 설정을 통째로 반영한다. 화면의 JSON 편집기가 쓰는 길이다.

    `apply` 와 규칙이 다르다. `apply` 는 기동할 때 도는 것이라 **없는 것만** 만들지만,
    여기는 사람이 화면에서 보고 고쳐 누른 것이라 **보이는 것이 곧 진실**이어야 한다.
    지운 줄은 지워져야 한다.

    다만 탐지 항목은 지우지 않는다. 항목을 지우면 그 항목의 **이벤트가 함께 지워진다**
    (FK CASCADE). JSON 에서 몇 글자 지운 것이 몇 만 건을 날리는 일이 되어서는 안 된다.
    빠진 항목이 있으면 거부하고 무엇이 걸리는지 알려 준다 — 정말 지울 거라면 관리자
    화면에서 결과를 보고 지우면 된다.

    validate 는 바인딩 한 건을 검사하는 코루틴(api.bindings._validate)을 받는다.
    검사 규칙을 두 벌로 갈라 두지 않으려고 넘겨받는다 — 이 계층은 API 를 모른다.
    """
    if not isinstance(data, dict):
        raise ConfigError("최상위가 객체가 아닙니다")

    incoming_sols = data.get("solutions")
    incoming_binds = data.get("bindings")
    if not isinstance(incoming_sols, list) or not isinstance(incoming_binds, list):
        raise ConfigError("solutions 와 bindings 는 배열이어야 합니다")
    runtime = data.get("runtime") or {}
    if not isinstance(runtime, dict):
        raise ConfigError("runtime 은 객체여야 합니다")

    # ── 탐지 항목: 넣고 고치되 지우지 않는다 ──
    codes: set[str] = set()
    for spec in incoming_sols:
        code = str(spec.get("code") or "").strip()
        if not code:
            raise ConfigError("탐지 항목에 code 가 없습니다")
        codes.add(code)
        fields = {k: v for k, v in spec.items() if k in _SOLUTION_FIELDS}
        row = await session.get(Solution, code)
        if row is None:
            session.add(Solution(**fields))
        else:
            for k, v in fields.items():
                setattr(row, k, v)

    existing = (await session.execute(select(Solution.code))).scalars().all()
    dropped = sorted(set(existing) - codes)
    if dropped:
        raise ConfigError(
            "탐지 항목은 여기서 지울 수 없습니다: " + ", ".join(dropped)
            + " — 항목을 지우면 그 항목의 이벤트도 함께 지워집니다. "
              "관리자 화면에서 지우세요.")

    # ── 바인딩: 보이는 것이 곧 전부 ──
    names: set[str] = set()
    for spec in incoming_binds:
        name = str(spec.get("name") or "").strip()
        if not name:
            raise ConfigError("바인딩에 name 이 없습니다")
        if name in names:
            raise ConfigError(f"바인딩 이름이 겹칩니다: {name}")
        names.add(name)
        fields = {k: v for k, v in spec.items() if k in _BINDING_FIELDS}
        fields["name"] = name
        if validate is not None:
            await validate(session, fields)
        row = await session.scalar(select(InboundBinding).where(InboundBinding.name == name))
        if row is None:
            session.add(InboundBinding(**fields))
        else:
            for k, v in fields.items():
                setattr(row, k, v)

    for row in (await session.execute(select(InboundBinding))).scalars().all():
        if row.name not in names:
            await session.delete(row)

    # ── 운영 설정: 통째로 갈아 끼운다(merge 가 아니다 — 지운 키는 지워져야 한다) ──
    await store.replace_value(session, store.KEY_RUNTIME, runtime)

    await session.commit()
    return await export(session)

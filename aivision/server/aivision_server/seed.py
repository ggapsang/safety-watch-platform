"""초기 데이터 — 클론한 사람이 곧바로 같은 것을 보게 만든다.

**왜 이것이 필요한가.** 바인딩과 탐지 항목은 DB 에만 있었다. 개발 PC 의 DB 에서
손으로 만들고 화면에서 확인했으니 '된다' 고 판단했는데, 저장소를 클론한 쪽은 빈 DB 로
뜬다 — 박스도 안 그려지고 이벤트도 안 걸린다. 코드는 다 있는데 아무것도 동작하지
않는 상태였다. 배선이 코드 밖에 있으면 그것은 만들어 둔 것이 아니다.

**왜 그럼에도 탐지 항목은 심지 않는가.** 화재·PPE 같은 그럴듯한 목록을 미리 넣으면
현장과 맞지 않는 항목이 화면에 남는다. 여기 넣는 것은 **우리가 받기로 이미 약속한
것뿐**이다 — 카메라 엣지가 토픽 `invasion` 으로 보내기로 한 침입.

**왜 매 기동마다 도는가.** 없는 것만 만든다(코드·이름으로 조회). 이미 있으면
손대지 않으므로, 운영 중에 어드민이 고친 값을 되돌리지 않는다. 지운 것도 되살린다 —
지웠는데 다시 생기는 것이 싫으면 켬/끔(enabled)으로 끄면 남는다.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Camera, InboundBinding, Solution

log = logging.getLogger(__name__)

# 카메라 엣지가 보내기로 한 것. 토픽 invasion, 페이로드 "someone invasion".
INVASION = "INVASION"

# 우리 모듈이 쓰는 토픽 모양. 이 바인딩도 다른 바인딩과 완전히 같은 데이터다 —
# 특권이 없고, 지우면 없어지고, 화면에서 고칠 수 있다.
_BINDINGS: list[dict] = [
    {
        # 박스 그리기. **이벤트가 아니다.** 사람이 서 있는 동안 초당 여러 번 나오는
        # 판독을 적재하면 DB 가 무너진다. live_only 가 그것을 막는다.
        "name": "라이브 박스",
        "topic_pattern": "aivision/live/+",
        "live_only": True,
        "priority": 10,
        "camera_from": "topic_segment", "camera_expr": "$topic[2]",
        "item_from": "fixed", "solution_code": None,   # 라이브 전용은 항목이 필요 없다
        "boxes_expr": "$.boxes", "boxes_format": "xyxy_norm",
        "module_expr": "$.module_id",
    },
    {
        # 모듈 판정 -> 이벤트. 항목 코드는 페이로드가 들고 온다(CLASS_MAP 이 옮긴 값).
        # 그 코드의 탐지 항목이 아직 없으면 걸리지 않는다 — 항목을 먼저 만들면 된다.
        "name": "모듈 판정",
        "topic_pattern": "aivision/detect/#",
        "priority": 20,
        "camera_from": "payload", "camera_expr": "$.camera_id",
        "item_from": "payload", "item_expr": "$.item",
        "state_expr": "$.state", "state_active": "active", "state_inactive": "inactive",
        "confidence_expr": "$.confidence", "ts_expr": "$.ts",
        "boxes_expr": "$.boxes", "boxes_format": "xyxy_norm",
        "module_expr": "$.module_id",
    },
    {
        # 카메라 엣지의 침입 알림. 페이로드에 카메라를 알 단서가 없어(고정 문구뿐)
        # 카메라를 바인딩에 못 박는다 — 아래에서 채운다.
        "name": "invasion",
        "topic_pattern": "invasion",
        "priority": 30,
        "camera_from": "fixed",
        "item_from": "fixed", "solution_code": INVASION,
        "state_expr": "",                    # 수신 자체가 발생
    },
]


async def seed(session: AsyncSession) -> None:
    made: list[str] = []

    if await session.get(Solution, INVASION) is None:
        session.add(Solution(code=INVASION, name="침입", short_name="침입",
                             description="카메라 엣지가 발행하는 침입 알림",
                             event_type="침입 감지", color="#c64545", sort_order=10))
        made.append(f"탐지 항목 {INVASION}")

    # 카메라가 하나뿐이면 invasion 을 거기에 붙인다. 여럿이면 고를 수 없으므로
    # 비워 두고 꺼 둔다 — 어드민이 카메라를 고르고 켜면 된다.
    cams = (await session.execute(select(Camera.id).order_by(Camera.id))).scalars().all()
    only_cam = cams[0] if len(cams) == 1 else None

    for spec in _BINDINGS:
        name = spec["name"]
        found = await session.scalar(select(InboundBinding).where(InboundBinding.name == name))
        if found is not None:
            continue
        row = dict(spec)
        if row.get("camera_from") == "fixed":
            row["camera_id"] = only_cam
            row["enabled"] = only_cam is not None
        session.add(InboundBinding(**row))
        made.append(f"바인딩 '{name}'"
                    + ("" if row.get("enabled", True) else " (카메라 지정 필요 — 꺼 둠)"))

    if not made:
        return
    await session.commit()
    log.info("기본 배선 생성: %s", ", ".join(made))

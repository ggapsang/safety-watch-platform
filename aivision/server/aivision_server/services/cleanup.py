"""지울 때 딸린 것을 어떻게 할지 — 코드가 정한다.

스키마에 외래키를 두지 않는다(개발 원칙). 그래서 부모를 지울 때 자식을 어떻게 할지
DB 가 대신 결정해 주지 않는다. **여기가 그 결정을 적어 두는 곳**이다.

외래키의 ON DELETE 는 편하지만 두 가지가 나쁘다. 무엇이 함께 사라지는지 코드를 읽어서는
알 수 없고(스키마를 따로 봐야 한다), 지우는 순간까지 아무도 그 사실을 모른다. 실제로
`events.solution_code` 에 걸어 둔 CASCADE 때문에 탐지 항목 하나를 지우면 그 항목의 사고
이력이 통째로 날아가는 구조였다. 여기 함수 이름과 본문으로 드러나 있으면 그런 것을
설계할 때 눈에 띈다.

정리는 두 갈래다.

    지운다     부모가 없으면 뜻이 없는 것 — 이벤트·박스·연결 행·전송 이력
    끊는다     부모가 없어도 남아야 하는 것 — 녹화 파일, 수신 원문 로그

'끊는다' 는 참조만 NULL 로 만든다. 녹화 파일은 디스크에 실제로 있고, 원문 로그는
'무엇이 왔었나' 의 기록이라 카메라를 치웠다고 사라지면 안 된다.

주의: 커밋하지 않는다. 부르는 쪽이 자기 트랜잭션 안에서 함께 커밋해야 카메라만 지워지고
자식이 남는 중간 상태가 생기지 않는다.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (CameraSolution, Event, EventBox, InboundBinding, ModuleAssignment,
                      MqttMessage, OutboundDelivery, Recording)

log = logging.getLogger(__name__)


async def _drop_events(session: AsyncSession, event_ids: list[int]) -> int:
    """이벤트와 그에 딸린 것들. 박스는 이벤트 없이는 뜻이 없어 함께 지운다."""
    if not event_ids:
        return 0
    await session.execute(delete(EventBox).where(EventBox.event_id.in_(event_ids)))
    await session.execute(delete(OutboundDelivery).where(
        OutboundDelivery.event_id.in_(event_ids)))
    # 녹화는 지우지 않는다. 파일이 디스크에 실제로 있고, 사고 클립은 이벤트 행보다
    # 오래 남아야 한다. 참조만 끊는다.
    await session.execute(update(Recording).where(Recording.event_id.in_(event_ids))
                          .values(event_id=None))
    result = await session.execute(delete(Event).where(Event.id.in_(event_ids)))
    return result.rowcount or 0


async def delete_camera(session: AsyncSession, camera_id: int) -> dict[str, int]:
    """카메라를 지운다. 이 카메라를 가리키던 것을 전부 정리한다.

    이력을 남기고 싶으면 삭제 대신 `enabled=false` 를 쓴다 — 이 함수는 정말로 지운다.
    """
    ids = (await session.execute(
        select(Event.id).where(Event.camera_id == camera_id))).scalars().all()
    events = await _drop_events(session, list(ids))

    await session.execute(delete(CameraSolution).where(CameraSolution.camera_id == camera_id))
    await session.execute(delete(ModuleAssignment).where(ModuleAssignment.camera_id == camera_id))
    # 바인딩은 이 카메라에 못 박아 둔 것만. 토픽으로 카메라를 찾는 바인딩은 남는다.
    binds = await session.execute(delete(InboundBinding).where(
        InboundBinding.camera_id == camera_id))
    # 녹화와 원문 로그는 남기고 참조만 끊는다.
    recs = await session.execute(update(Recording).where(Recording.camera_id == camera_id)
                                 .values(camera_id=None))
    await session.execute(update(MqttMessage).where(MqttMessage.camera_id == camera_id)
                          .values(camera_id=None))

    counts = {"events": events, "bindings": binds.rowcount or 0,
              "recordings_detached": recs.rowcount or 0}
    log.info("카메라 #%d 정리: 이벤트 %d건 삭제 · 바인딩 %d건 삭제 · 녹화 %d건 참조 해제",
             camera_id, counts["events"], counts["bindings"], counts["recordings_detached"])
    return counts


async def delete_module(session: AsyncSession, module_id: str) -> int:
    """모듈을 지운다. 카메라 할당만 함께 정리한다.

    이 모듈이 만든 **이벤트는 지우지 않는다.** 이벤트의 module_id 는 '무엇이 판정했나'
    를 남기는 기록이지 소유 관계가 아니다. 모듈을 갈아 끼웠다고 지난 판정이 없던 일이
    되면 안 된다.
    """
    result = await session.execute(delete(ModuleAssignment).where(
        ModuleAssignment.module_id == module_id))
    n = result.rowcount or 0
    log.info("모듈 %s 정리: 할당 %d건 삭제", module_id, n)
    return n


async def delete_outbound_target(session: AsyncSession, target_id: int) -> int:
    """아웃바운드 대상을 지운다. 그 대상으로 보낸 전송 이력도 함께.

    대상이 없어진 전송 이력은 '어디로 보냈는지 모르는 기록' 이라 남겨도 읽을 수 없다.
    """
    result = await session.execute(delete(OutboundDelivery).where(
        OutboundDelivery.target_id == target_id))
    n = result.rowcount or 0
    log.info("아웃바운드 대상 #%d 정리: 전송 이력 %d건 삭제", target_id, n)
    return n


async def solution_usage(session: AsyncSession, code: str) -> int:
    """이 탐지 항목으로 기록된 이벤트가 몇 건인가. 지우기 전에 물어보는 자리다."""
    return int(await session.scalar(
        select(func.count()).select_from(Event).where(Event.solution_code == code)) or 0)


async def delete_solution(session: AsyncSession, code: str, *, force: bool) -> dict[str, int]:
    """탐지 항목을 지운다.

    **이벤트가 있으면 기본적으로 거부한다.** 탐지 항목은 분류 이름일 뿐인데, 예전에는
    외래키 CASCADE 로 묶여 있어 이름 하나 정리하려다 사고 이력이 통째로 날아갔다.
    안전관리 기록을 다루는 플랫폼에서 그 동작이 기본값이어서는 안 된다.

    정말 이력째 지우려면 force 로 분명히 말해야 한다.
    """
    used = await solution_usage(session, code)
    if used and not force:
        raise ValueError(f"이 탐지 항목으로 기록된 이벤트가 {used}건 있습니다. "
                         "이력째 지우려면 강제 삭제를 선택하세요")

    events = 0
    if used:
        ids = (await session.execute(
            select(Event.id).where(Event.solution_code == code))).scalars().all()
        events = await _drop_events(session, list(ids))

    await session.execute(delete(CameraSolution).where(CameraSolution.solution_code == code))
    # 이 항목을 가리키던 바인딩은 남기고 참조만 끊는다. 토픽과 표현식은 손으로 맞춘
    # 것이라, 항목을 지웠다고 함께 날리면 다시 만드는 값이 크다. 화면에는 '탐지 항목
    # 없음' 으로 드러난다.
    binds = await session.execute(update(InboundBinding)
                                  .where(InboundBinding.solution_code == code)
                                  .values(solution_code=None, enabled=False))
    counts = {"events": events, "bindings_detached": binds.rowcount or 0}
    log.info("탐지 항목 %s 정리: 이벤트 %d건 삭제 · 바인딩 %d건 참조 해제 후 끔",
             code, counts["events"], counts["bindings_detached"])
    return counts

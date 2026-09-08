"""초기 데이터.

지금은 **아무것도 넣지 않는다.**

탐지 항목(솔루션)과 그 판정 규칙은 현장에서 무엇을 볼지 정해진 뒤에 하나씩 만든다.
그럴듯한 기본값(화재·PPE 같은)을 미리 심어 두면 현장과 맞지 않는 항목이 화면에 남고,
나중에 진짜 항목이 정해졌을 때 그것을 지우는 일부터 해야 한다.

지금 단계에서 서버가 하는 일은 다음 두 가지다.
  · 등록된 카메라의 RTSP 영상을 받아 웹으로 재송출한다.
  · 카메라가 보내는 MQTT 메시지를 원문 그대로 받아 보관한다('MQTT 로그' 화면).

무엇을 이벤트로 볼지는 그 원문을 보고 정한 다음, solutions / detection_rules 에
데이터를 넣으면 된다. 코드 수정은 필요 없다.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


async def seed(session: AsyncSession) -> None:      # noqa: ARG001
    """자리만 유지한다. 넣을 기본 데이터가 생기면 여기에 쓴다."""
    return None

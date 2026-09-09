"""초기 데이터 — 설정 파일을 DB 로 붓는다.

여기에 데이터를 적어 두지 않는다. 배선(탐지 항목·인바운드 바인딩·운영 설정)은
`deploy/config/platform.json` 이 원본이고, 이 함수는 기동할 때 그것을 DB 로 옮길
뿐이다. 왜 파일이 원본인지는 `services/config_file.py` 의 첫머리에 적어 두었다 —
요약하면 **DB 는 커밋할 수 없기 때문**이다.

없는 것만 만든다. 화면에서 고친 값을 기동할 때마다 되돌리지 않는다.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .services import config_file

log = logging.getLogger(__name__)


async def seed(session: AsyncSession) -> None:
    made = await config_file.apply(session, get_settings().config_file)
    if made:
        log.info("설정 파일에서 배선 생성: %s", ", ".join(made))

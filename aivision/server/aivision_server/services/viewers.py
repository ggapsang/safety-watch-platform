"""지금 누가 어느 카메라를 보고 있나.

**왜 필요한가.** 추론은 비싸다. 카메라 여섯 대를 전부 돌리면 자원이 금세 찬다. 그런데
화면에 떠 있는 것은 대개 한두 대다 — 아무도 안 보는 카메라를 같은 속도로 돌릴 이유가
없다. 이 표를 보고 모듈이 보는 카메라만(또는 그쪽을 더 자주) 처리한다.

**DB 에 넣지 않는다.** 초 단위로 바뀌고 서버가 재시작하면 뜻이 없어지는 값이다. 그런
것을 DB 에 쓰면 쓰기만 늘고 얻는 것이 없다.

**시간이 지나면 저절로 지워진다.** 브라우저는 탭을 닫으면서 '이제 안 봅니다' 를 보내 줄
수 없다(닫히는 중에 요청이 끊긴다). 그래서 '본다' 만 주기적으로 받고, 소식이 끊기면
안 보는 것으로 친다. 끝을 알리는 신호에 기대면 언젠가 영원히 보는 중인 카메라가 남는다.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

# 이 시간 안에 소식이 없으면 안 보는 것으로 친다. 브라우저가 5초마다 알리므로
# 한두 번 놓쳐도 깜빡이지 않을 만큼 여유를 둔다.
TTL_SEC = 20.0

_seen: dict[int, float] = {}


def report(camera_ids: list[int]) -> None:
    """브라우저가 '지금 이것들을 보고 있다' 고 알려 온다."""
    now = time.monotonic()
    for cid in camera_ids:
        _seen[cid] = now


def watching() -> set[int]:
    """지금 누군가 보고 있는 카메라. 낡은 것은 지우면서 돌려준다."""
    now = time.monotonic()
    stale = [cid for cid, t in _seen.items() if now - t > TTL_SEC]
    for cid in stale:
        del _seen[cid]
    return set(_seen)


def is_watching(camera_id: int) -> bool:
    return camera_id in watching()

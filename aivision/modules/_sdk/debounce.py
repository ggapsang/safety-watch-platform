"""전이 검출 — 매 프레임이 아니라 '바뀌는 순간'만 남긴다.

왜 이것이 코어가 아니라 모듈의 책임인가. 3fps 로만 봐도 카메라 2대면 하루 50만 프레임이다.
프레임마다 발행하면 브로커와 DB 가 무너진다. 플랫폼의 중복억제는 같은 이벤트가 짧은 시간에 여러 번
들어오는 것을 막는 안전장치이지, 초당 여러 번을 흘려보내도 된다는 뜻이 아니다.

규칙
  · 올라갈 때는 즉시 (발생을 늦추면 안전관리에서는 그게 사고다)
  · 내려갈 때는 hold_sec 동안 한 번도 안 보인 뒤 (한두 프레임 놓친 것으로 해제하면 깜빡인다)

상태는 (카메라, 탐지항목) 쌍마다 따로 둔다. 같은 카메라에서 항목 둘이 동시에 활성일 수 있다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Transition:
    camera_id: int
    item: str
    state: str                       # "active" | "inactive"
    confidence: float
    boxes: list[dict] = field(default_factory=list)


@dataclass
class _State:
    active: bool = False
    last_seen: float = 0.0
    best_conf: float = 0.0
    boxes: list[dict] = field(default_factory=list)


class Debouncer:
    """항목별 활성/해제 전이를 만든다.

    한 프레임 결과를 `observe()` 에 넘기면, 그 프레임이 만든 전이만 돌려준다.
    아무 것도 바뀌지 않으면 빈 목록이다.
    """

    def __init__(self, hold_sec: float = 3.0, min_conf: float = 0.4) -> None:
        self.hold_sec = hold_sec
        self.min_conf = min_conf
        self._states: dict[tuple[int, str], _State] = {}

    def observe(self, camera_id: int, now: float,
                found: dict[str, list[dict]]) -> list[Transition]:
        """`found` 는 항목코드 -> 박스 목록. 이번 프레임에서 본 것만 담는다."""
        out: list[Transition] = []

        for item, boxes in found.items():
            conf = max((b.get("score") or 0.0 for b in boxes), default=0.0)
            if conf < self.min_conf:
                # 봤지만 확신이 낮다. 활성 유지에도 쓰지 않는다 — 낮은 신뢰도로
                # 해제를 무한히 미루면 이벤트가 영원히 끝나지 않는다.
                continue
            st = self._states.setdefault((camera_id, item), _State())
            st.last_seen = now
            st.boxes = boxes
            st.best_conf = max(st.best_conf, conf)
            if not st.active:
                st.active = True
                out.append(Transition(camera_id, item, "active", conf, boxes))
                log.info("발생: 카메라 %d / %s (conf %.2f)", camera_id, item, conf)

        # 이번 프레임에서 못 본 것들의 해제 판정
        for (cam, item), st in list(self._states.items()):
            if cam != camera_id or not st.active:
                continue
            if st.last_seen == now:      # 이번 프레임에서 봤다(신뢰도 통과분)
                continue
            if now - st.last_seen >= self.hold_sec:
                st.active = False
                out.append(Transition(cam, item, "inactive", st.best_conf, st.boxes))
                log.info("해제: 카메라 %d / %s (%.0f초 미검출)", cam, item, self.hold_sec)
                st.best_conf = 0.0
                st.boxes = []

        return out

    def close_camera(self, camera_id: int, now: float) -> list[Transition]:
        """카메라 할당이 사라지거나 스트림이 끊길 때 활성 상태를 정리한다.

        이것이 없으면 '발생' 만 남고 '해제' 가 오지 않아 화면에 영원히 켜져 있다.
        """
        out: list[Transition] = []
        for (cam, item), st in list(self._states.items()):
            if cam != camera_id:
                continue
            if st.active:
                st.active = False
                out.append(Transition(cam, item, "inactive", st.best_conf, st.boxes))
                log.info("해제(스트림 종료): 카메라 %d / %s", cam, item)
            self._states.pop((cam, item), None)
        return out

    def active_items(self) -> list[tuple[int, str]]:
        """heartbeat 에 실어 보낼 현재 활성 목록."""
        return sorted(k for k, v in self._states.items() if v.active)

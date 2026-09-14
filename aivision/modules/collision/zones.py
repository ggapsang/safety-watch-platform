"""위험 구역을 그림으로 — 판정이 쓰는 기하를 그대로 이미지에 되돌린다.

**왜 필요한가.** 지금까지 이 모듈이 내보낸 것은 '위험하다' 는 판정뿐이었다. 사람은
그것을 믿을 근거가 없다 — 왜 저 사람이 위험하고 옆 사람은 아닌지 화면에 아무것도
없기 때문이다. 구역을 그리면 판정이 **설명 가능해진다.** 임계값을 만질 때도 숫자가
아니라 구역이 커지고 줄어드는 것을 보고 맞출 수 있다.

두 가지를 그린다(기획 6장의 비대칭 설계 그대로).

  `corridor`  AMR 진행 방향의 통로. 판정이 '주의' 를 매기는 그 직사각형이다.
  `reach`     사람의 최악 도달 반경 R_h(τ)=r_h+σ+v_h,max·τ. 바닥 위의 원이다.

둘이 겹치는 것이 곧 임박에 가까워지는 것이다 — AMR 이 몸으로 쓸고 가는 길과 사람이
어디로든 갈 수 있는 범위가 만나는 지점이기 때문이다.

**기하는 `judge` 에서 가져온다.** 여기서 다시 계산하지 않는다. 그림이 판정과 다른
숫자를 쓰면 구역 밖에 선 사람에게 알람이 울리고, 그 화면은 아무도 믿지 않는다.

**좌표는 두 층이다.**
  · `box`    계약이 아는 유일한 모양(0~1 정규화 축정렬 사각형, 계약 2장). 코어 화면이
             오늘 그대로 그릴 수 있다.
  · `points` 원근이 살아 있는 다각형. 계약 밖의 확장 필드라 코어는 무시하고, 이 모듈의
             화면과 나중에 이것을 읽기로 한 쪽만 쓴다.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import geometry
import judge
from settings import Thresholds
from tracking import AMR, PERSON, Track

log = logging.getLogger(__name__)

CORRIDOR = "corridor"
REACH = "reach"

# 구역 박스에 붙는 이름. 계약은 '사람이 읽을 이름' 을 요구한다(항목 코드가 아니라).
# 이벤트 상세창과 대시보드에 이 글자가 그대로 그려진다.
NAMES = {CORRIDOR: "위험구역", REACH: "도달범위"}
LEVEL_NAMES = {judge.WARN: "주의", judge.IMMINENT: "임박"}


@dataclass
class Zone:
    """그릴 수 있는 구역 하나."""

    kind: str                        # corridor | reach
    track_id: int
    level: str = ""                  # "" | warn | imminent — 색을 가르는 값
    points: list[tuple[float, float]] = field(default_factory=list)   # 정규화 이미지
    world: dict = field(default_factory=dict)                         # 미터 단위 원본

    @property
    def label(self) -> str:
        who = "AMR" if self.kind == CORRIDOR else "사람"
        tail = LEVEL_NAMES.get(self.level, "")
        name = f"{NAMES[self.kind]} {who}#{self.track_id}"
        return f"{name} · {tail}" if tail else name

    def box(self) -> dict | None:
        """계약이 아는 모양. 구역이 화면 밖으로 밀려났으면 None 이다.

        `score` 를 넣지 않는다. 이것은 탐지가 아니라 **설명**이고, 점수를 붙이면 화면이
        '0.9 확률로 위험구역' 이라고 읽히게 된다. 계약도 score 는 선택이라고 적는다.
        """
        bbox = geometry.bounding_box(self.points)
        if bbox is None:
            return None
        x1, y1, x2, y2 = bbox
        return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "label": self.label}

    def shape(self) -> dict:
        """확장 필드. 원근이 살아 있는 다각형과 미터 단위 원본을 함께 준다."""
        return {"kind": self.kind, "track_id": self.track_id, "level": self.level,
                "label": self.label,
                "points": [[round(u, 5), round(v, 5)] for u, v in self.points],
                "world": self.world}


def levels_by_track(pairs: list[judge.Pair]) -> tuple[dict[int, str], dict[int, str]]:
    """트랙마다 지금 걸려 있는 가장 높은 수준. -> (AMR 표, 사람 표)

    구역 색과 박스 라벨이 같은 표를 본다. 따로 구하면 '박스는 임박인데 구역은 주의 색'
    같은 화면이 나온다.
    """
    by_amr: dict[int, str] = {}
    by_person: dict[int, str] = {}
    for p in pairs:
        if not p.level:
            continue
        for table, track in ((by_amr, p.amr), (by_person, p.person)):
            if judge.RANK[p.level] > judge.RANK[table.get(track.track_id, "")]:
                table[track.track_id] = p.level
    return (by_amr, by_person)


def build(persons: list[Track], amrs: list[Track], pairs: list[judge.Pair],
          tun: Thresholds, H_inv) -> list[Zone]:
    """지금 화면에 그릴 구역 전부.

    `H_inv` 가 없으면(미보정) 빈 목록이다 — 미터를 이미지로 되돌릴 방법이 없다.
    """
    if H_inv is None:
        return []
    by_amr, by_person = levels_by_track(pairs)

    out: list[Zone] = []
    for amr in amrs:
        zone = corridor_zone(amr, tun, H_inv, by_amr.get(amr.track_id, ""))
        if zone is not None:
            out.append(zone)
    for person in persons:
        zone = reach_zone(person, tun, H_inv, by_person.get(person.track_id, ""))
        if zone is not None:
            out.append(zone)
    return out


def corridor_zone(amr: Track, tun: Thresholds, H_inv, level: str) -> Zone | None:
    """AMR 진행 방향 통로. 판정의 `corridor_of` 와 같은 직사각형이다."""
    if amr.kind != AMR:
        return None
    heading, half_w, length, back = judge.corridor_of(amr, tun)
    quad = geometry.corridor_quad(amr.pos, heading, half_w, length, back)
    if not quad:
        # 한 번도 움직인 적이 없어 방향을 모른다. 방향 없는 통로는 그릴 수 없다 —
        # 원으로 대신 그리면 판정과 다른 그림이 되므로 아무것도 그리지 않는다.
        return None
    points = geometry.project_polygon(H_inv, quad, amr.pos)
    if not points:
        return None
    return Zone(kind=CORRIDOR, track_id=amr.track_id, level=level, points=points,
                world={"length_m": round(length, 2),
                       "half_width_m": round(half_w, 2),
                       "speed_ms": round(amr.speed, 2),
                       "heading_deg": round(math.degrees(math.atan2(heading[1],
                                                                    heading[0])), 1)})


def reach_zone(person: Track, tun: Thresholds, H_inv, level: str) -> Zone | None:
    """사람의 최악 도달 반경. 바닥에 그리는 원이다(원근을 지나면 타원이 된다)."""
    if person.kind != PERSON:
        return None
    radius = judge.reach_radius(tun)
    circle = geometry.circle_points(person.pos, radius)
    points = geometry.project_polygon(H_inv, circle, person.pos)
    if not points:
        return None
    return Zone(kind=REACH, track_id=person.track_id, level=level, points=points,
                world={"radius_m": round(radius, 2),
                       "horizon_sec": round(tun.t_imminent, 2),
                       "center": [round(person.pos[0], 2), round(person.pos[1], 2)]})

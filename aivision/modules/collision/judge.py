"""판정 — 특징량 · 위험 알람 · 충돌 확정.

여기에는 MQTT 도 HTTP 도 없다. 들어오는 것은 트랙 목록과 임계값이고, 나오는 것은
'무엇이 위험한가' 와 '충돌이 났는가' 뿐이다. 그래서 브로커도 카메라도 없이 tests.py 에서
시나리오를 그대로 돌려 볼 수 있다.

두 판정의 성격이 다르다.

  **위험 알람**(6장)은 비대칭이다. AMR 은 관측된 속도로 앞을 쓸고, 사람은 어느 방향으로든
  갈 수 있다고 본다. 사람의 관측 속도를 믿고 '안전' 이라고 말하면, 틀렸을 때 사람이 다친다.

  **충돌 확정**(7장)은 보수적이다. 접촉만으로는 확정하지 않는다 — 박스가 겹치는 일은
  하루에도 수없이 일어난다. 접촉과 충격이 **도착 시각 기준 Δt 창 안에서 같이** 관측될
  때만 확정한다. 텔레메트리 같은 독립 채널이 없으므로 이것이 우리가 가진 최선이다.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import geometry
from settings import Thresholds
from tracking import Track

log = logging.getLogger(__name__)

WARN = "warn"
IMMINENT = "imminent"
RANK = {"": 0, WARN: 1, IMMINENT: 2}

GRAZE = "graze"
KNOCKDOWN = "knockdown"
SEVERE = "severe"
GRADE_NAMES = {GRAZE: "접촉·스침", KNOCKDOWN: "충돌·전도", SEVERE: "심각"}

# 이보다 느린 AMR 은 '서 있다' 고 본다. 서 있는 AMR 에 임박을 내지 않는 이유:
# 임박은 '전방 스윕이 도달한다' 는 판정인데 스윕할 속도가 없다. 서 있는 AMR 앞에 선
# 사람은 주의(통로 안)로 남는다 — 언제 출발할지 모르므로 알람을 아주 끄지는 않는다.
MOVING = 0.1


@dataclass
class Pair:
    """사람 한 명과 AMR 한 대의 관계. 판정의 최소 단위다."""

    person: Track
    amr: Track
    dist: float                      # 바닥 투영 거리 (m)
    v_rel: float                     # 상대속도 크기 (m/s)
    closing: float                   # 접근 속도. 음수면 멀어지는 중
    t_cpa: float                     # 최근접 예상 시각 (s, 등속 가정)
    d_cpa: float                     # 그때의 거리 (m)
    ahead: float                     # AMR 진행 방향 성분 (m)
    lateral: float                   # 통로 중심선에서 옆으로 (m)
    level: str = ""                  # "" | warn | imminent
    tau: float | None = None         # 임박까지 남은 시간 (s)

    @property
    def key(self) -> tuple[int, int]:
        return (self.person.track_id, self.amr.track_id)

    def boxes(self) -> list[dict]:
        """이벤트에 붙일 박스. 발행자가 준 것을 그대로 쓴다 — 좌표는 이미 0~1 이고
        라벨은 사람이 읽을 이름이다(계약 2장)."""
        return [dict(self.person.box), dict(self.amr.box)]

    def confidence(self) -> float:
        """두 박스 중 낮은 쪽. 쌍으로 내리는 판정이므로 약한 쪽이 이 판정의 확신이다."""
        return min(self.person.score(), self.amr.score())


def pair_of(person: Track, amr: Track, tun: Thresholds) -> Pair:
    """특징량(기획 5.4)을 한 번에 뽑는다."""
    px, py = person.pos
    ax, ay = amr.pos
    dp = (px - ax, py - ay)
    dist = math.hypot(*dp)

    pvx, pvy = person.vel
    avx, avy = amr.vel
    dv = (pvx - avx, pvy - avy)
    v_rel = math.hypot(*dv)
    # 거리의 변화율. 양수면 가까워지는 중이다.
    closing = -((dp[0] * dv[0] + dp[1] * dv[1]) / dist) if dist > 1e-9 else 0.0
    t_cpa, d_cpa = geometry.closest_approach(dp, dv)

    _, ahead, lateral = geometry.corridor_hit(
        amr.pos, heading_of(amr), person.pos, tun.corridor_half_w, 0.0)
    return Pair(person=person, amr=amr, dist=dist, v_rel=v_rel, closing=closing,
                t_cpa=t_cpa, d_cpa=d_cpa, ahead=ahead, lateral=lateral)


def assess(persons: list[Track], amrs: list[Track], tun: Thresholds) -> list[Pair]:
    """모든 사람×AMR 쌍의 위험 수준을 매긴다.

    쌍을 전부 보는 것이 낭비처럼 보이지만, 한 카메라에 AMR 몇 대와 사람 몇 명이다.
    '가까운 것만' 고르려면 어차피 전부 한 번씩 재야 한다.
    """
    out: list[Pair] = []
    for person in persons:
        for amr in amrs:
            p = pair_of(person, amr, tun)
            p.level, p.tau = _level(p, tun)
            out.append(p)
    return out


def heading_of(amr: Track) -> tuple[float, float]:
    """AMR 의 진행 방향. 서 있으면 마지막으로 움직이던 쪽을 쓴다."""
    return amr.heading if math.hypot(*amr.heading) > 1e-9 else amr.vel


def corridor_of(amr: Track, tun: Thresholds) -> tuple[tuple[float, float], float,
                                                      float, float]:
    """주의 통로의 기하 — (진행 방향, 반폭, 길이, 뒤쪽 여유). 전부 미터.

    **판정과 그림이 이 함수 하나를 같이 쓴다.** 통로를 그리는 쪽이 따로 계산하면
    언젠가 한쪽만 고쳐지고, 그때 화면은 '구역 밖에 선 사람에게 울리는 알람' 이 된다.
    """
    # 통로 길이 = AMR 몸체 + T 초 동안 갈 거리. 서 있어도 최소 길이는 남긴다 —
    # 스테이션 앞에 잠깐 선 AMR 앞에 서 있는 것은 여전히 주의할 일이다.
    length = max(tun.corridor_min_len, tun.r_amr + amr.speed * tun.t_warn)
    return (heading_of(amr), tun.corridor_half_w + tun.r_h, length, tun.r_amr)


def reach_radius(tun: Thresholds, tau: float | None = None) -> float:
    """사람 최악 도달 반경 R_h(τ) = r_h + σ + v_h,max·τ (기획 6장).

    기본은 임박 전망 시간에서의 값이다. 사람이 어느 방향으로든 갈 수 있다고 보는 쪽이
    이 반경이고, 이것이 임박 판정의 '사람 몫' 이다.
    """
    horizon = tun.t_imminent if tau is None else tau
    return tun.r_h + tun.sigma + tun.v_h_max * horizon


def _level(p: Pair, tun: Thresholds) -> tuple[str, float | None]:
    amr = p.amr
    speed = amr.speed

    # 임박 — 전방 스윕이 사람의 최악 도달 영역에 닿는가.
    tau = None
    if speed > MOVING:
        tau = geometry.sweep_tau(
            amr.pos, amr.vel, p.person.pos,
            radius=tun.r_amr + tun.r_h + tun.sigma,
            growth=tun.v_h_max, horizon=tun.t_imminent)
        if tau is not None:
            return (IMMINENT, tau)

    # 주의 — 사람이 AMR 통로 안에 있는가. 그리는 쪽과 같은 기하를 쓴다.
    heading, half_w, length, back = corridor_of(amr, tun)
    inside, _, _ = geometry.corridor_hit(
        amr.pos, heading, p.person.pos, half_w=half_w, length=length, back=back)
    if inside:
        return (WARN, tau)
    return ("", tau)


# ────────────────────────────────────────────────────────── 충격 관측

@dataclass
class Impact:
    t: float
    kind: str                        # amr_stop | person_dv | topple

    NAMES = {"amr_stop": "AMR 급정지", "person_dv": "사람 속도 급변", "topple": "전도(종횡비 반전)"}

    @property
    def name(self) -> str:
        return self.NAMES.get(self.kind, self.kind)


def amr_stop(track: Track, now: float, tun: Thresholds) -> Impact | None:
    """AMR 급정지 — 최근 최고 속력에서 지금 속력이 얼마나 떨어졌나.

    가속도를 칼만 상태에 넣지 않은 이유가 여기 있다. 필터가 충격을 흡수해 버리면
    급정지가 '부드러운 감속' 으로 보인다. 측정 시계열에서 직접 찾는다.
    """
    samples = track.recent(now, tun.dt_window)
    if len(samples) < 2:
        return None
    peak = max(s.speed for s in samples)
    last = samples[-1]
    if peak - last.speed >= tun.amr_stop_dv and peak > tun.amr_stop_dv:
        return Impact(last.t, "amr_stop")
    return None


def person_jolt(track: Track, now: float, tun: Thresholds) -> Impact | None:
    """사람 속도 스파이크 — 밀리거나 넘어지면 속력이 한 칸에 크게 바뀐다."""
    samples = track.recent(now, tun.dt_window)
    for prev, cur in zip(samples, samples[1:]):
        if abs(cur.speed - prev.speed) >= tun.person_dv:
            return Impact(cur.t, "person_dv")
    return None


def topple(track: Track, now: float, tun: Thresholds) -> Impact | None:
    """전도 — 서 있던 사람의 박스가 눕는다. 종횡비(w/h)가 뒤집힌다.

    단안 카메라에서 전도 순간에는 발 접지점이 무너져 거리도 속도도 못 믿는다(기획 10장).
    그때 종횡비는 좌표계와 무관한 몇 안 되는 단서다.
    """
    samples = track.recent(now, max(tun.dt_window, 1.0))
    if len(samples) < 3:
        return None
    # 기준선은 '충격 전' 이어야 한다. 마지막 두 칸을 빼고 중앙값을 잡는다.
    before = sorted(s.aspect for s in samples[:-2])
    if not before:
        return None
    baseline = before[len(before) // 2]
    last = samples[-1]
    if baseline > 1e-6 and last.aspect >= baseline * tun.aspect_flip:
        return Impact(last.t, "topple")
    return None


# ────────────────────────────────────────────────────────── 충돌 확정

@dataclass
class Episode:
    """한 쌍이 붙어 있는 동안의 기록. 확정은 이 안에서 한 번만 난다."""

    key: tuple[int, int]
    started: float
    last_seen: float
    contact_at: float = 0.0          # 접촉(ε 이내)을 마지막으로 본 시각
    peak_v_rel: float = 0.0
    confirmed_at: float = 0.0
    grade: str = ""
    impacts: list[Impact] = field(default_factory=list)
    pair: Pair | None = None

    @property
    def confirmed(self) -> bool:
        return self.confirmed_at > 0.0


@dataclass
class Confirmation:
    """확정 한 건. 발행과 화면이 이것만 보면 되도록 필요한 것을 다 담는다."""

    camera_id: int
    pair: Pair
    grade: str
    v_rel: float
    dist: float
    impacts: list[Impact]
    at: float

    def payload_extra(self) -> dict:
        return {"grade": self.grade, "v_rel": round(self.v_rel, 3),
                "distance": round(self.dist, 3),
                "signals": [i.kind for i in self.impacts]}


class CollisionWatcher:
    """카메라 한 대의 충돌 판독 상태.

    쌍(사람 트랙, AMR 트랙)마다 에피소드를 둔다. 같은 사고로 이벤트가 여러 번 나가면
    안 되므로 확정은 에피소드당 한 번이고, 둘이 충분히 떨어지면 에피소드를 닫는다.
    """

    def __init__(self, camera_id: int) -> None:
        self.camera_id = camera_id
        self.episodes: dict[tuple[int, int], Episode] = {}

    def feed(self, now: float, pairs: list[Pair],
             tun: Thresholds) -> list[Confirmation]:
        """이번 틱의 쌍들을 넣고, **이번에 새로 확정된 것만** 돌려준다."""
        out: list[Confirmation] = []
        release = tun.eps_contact * 2.0

        for p in pairs:
            ep = self.episodes.get(p.key)
            if p.dist > release:
                # 충분히 떨어졌다. 확정되지 않은 에피소드는 그냥 닫는다.
                if ep is not None and not ep.confirmed:
                    self.episodes.pop(p.key, None)
                elif ep is not None:
                    ep.last_seen = now
                    ep.pair = p
                continue

            if ep is None:
                ep = Episode(key=p.key, started=now, last_seen=now)
                self.episodes[p.key] = ep
            ep.last_seen = now
            ep.pair = p
            ep.peak_v_rel = max(ep.peak_v_rel, p.v_rel)

            if p.dist <= tun.eps_contact:
                ep.contact_at = now
            if ep.confirmed or ep.contact_at <= 0.0:
                continue

            impacts = [i for i in (amr_stop(p.amr, now, tun),
                                   person_jolt(p.person, now, tun),
                                   topple(p.person, now, tun)) if i is not None]
            # 동시성은 **도착 시각** 으로 판단한다. 발행자 ts 는 초 단위이고 시계도 다르다
            # (계약 3장) — 100ms 충격을 그 시각으로 맞추려 들면 틀린다.
            hits = [i for i in impacts if abs(i.t - ep.contact_at) <= tun.dt_window]
            if not hits:
                continue

            ep.impacts = hits
            ep.confirmed_at = now
            ep.grade = _grade(ep.peak_v_rel, hits, tun)
            out.append(Confirmation(camera_id=self.camera_id, pair=p, grade=ep.grade,
                                    v_rel=ep.peak_v_rel, dist=p.dist, impacts=hits,
                                    at=now))
            log.warning("충돌 확정: 카메라 %d · 사람#%d × AMR#%d · %s "
                        "(거리 %.2fm, 상대속도 %.2fm/s, 근거 %s)",
                        self.camera_id, p.person.track_id, p.amr.track_id,
                        GRADE_NAMES.get(ep.grade, ep.grade), p.dist, ep.peak_v_rel,
                        ", ".join(i.name for i in hits))
        return out

    def sweep(self, now: float, tun: Thresholds, alive: set[int]) -> None:
        """트랙이 사라졌거나 오래된 에피소드를 정리한다.

        이것이 없으면 확정된 에피소드가 영원히 남아 COLLISION 이 해제되지 않는다.
        """
        for key, ep in list(self.episodes.items()):
            person_id, amr_id = key
            stale = now - ep.last_seen > max(tun.hold_sec, tun.dt_window)
            if stale or person_id not in alive or amr_id not in alive:
                self.episodes.pop(key, None)

    def live(self) -> list[Episode]:
        """지금 '충돌 중' 으로 볼 에피소드. Debouncer 가 이것으로 활성을 유지한다."""
        return [e for e in self.episodes.values() if e.confirmed]


def _grade(v_rel: float, impacts: list[Impact], tun: Thresholds) -> str:
    """등급은 상대속도로 가르되, 전도가 관측되면 스침으로 내려가지 않는다.

    사람이 넘어졌는데 '접촉·스침' 으로 기록되면 그 이벤트는 아무도 다시 안 본다.
    """
    toppled = any(i.kind == "topple" for i in impacts)
    if v_rel >= tun.severe_v:
        return SEVERE
    if v_rel >= tun.graze_v or toppled:
        return KNOCKDOWN
    return GRAZE

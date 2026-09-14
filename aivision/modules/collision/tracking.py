"""자체 트래킹과 상태 추정 — 계약의 박스에는 ID 가 없다.

발행자(YOLO·카메라 내장 분석)는 **프레임 단위**로 본다. 이 프레임의 사람과 다음 프레임의
사람이 같은 사람인지는 말해 주지 않는다(계약 2장의 박스에 `track_id` 가 없다). 그런데
충돌 판정은 전부 시계열이다 — 속도도, 급정지도, 전도도 '어제와 오늘' 을 이어야 보인다.
그 연속성을 여기서 공급한다.

두 층으로 나눈다.

  `Tracker`   박스 IoU 로 같은 물체를 잇는다. 짧은 미검출은 견딘다.
  `Kalman2D`  이어진 시계열에서 **월드 좌표의 위치와 속도**를 뽑는다.

칼만을 이미지 좌표가 아니라 월드 좌표에 거는 이유: 이미지에서는 멀리 있는 물체가 느리게,
가까운 물체가 빠르게 움직여 같은 속도가 다른 숫자가 된다. 바닥 평면으로 옮긴 뒤에야
m/s 라는 하나의 단위로 비교할 수 있다.

ByteTrack 을 그대로 쓰지 않는 이유: 그 구현들은 픽셀 좌표·검출 점수 두 단계 매칭·
재식별 특징을 전제하는데, 우리에게 오는 것은 초당 몇 장의 정규화 박스뿐이다. 필요한
것(IoU 매칭 + 나이 관리)만 여기에 두면 의존성이 numpy 하나로 끝난다.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

AMR = "amr"
PERSON = "person"

# 가속도 잡음(m²/s⁴)과 측정 잡음(m). 사람은 한 걸음에 방향을 바꾸고, 호모그래피로 옮긴
# 발 접지점은 박스가 몇 픽셀만 흔들려도 십 수 cm 를 오간다. 이 두 값이 '얼마나 믿을지' 다.
PROCESS_NOISE = 1.5
MEAS_NOISE = 0.20


class Kalman2D:
    """등속 모델 칼만 — 상태는 [x, y, vx, vy].

    가속도를 상태에 넣지 않는다. AMR 의 급정지처럼 우리가 보고 싶은 것은 '모델이 예측하지
    못한 변화' 인데, 가속도를 상태에 넣으면 필터가 그 변화를 흡수해 버려 오히려 안 보인다.
    급정지는 필터가 아니라 속도 시계열에서 찾는다(judge.py).
    """

    def __init__(self, pos: tuple[float, float]) -> None:
        self.x = np.array([pos[0], pos[1], 0.0, 0.0], dtype=float)
        # 처음에는 위치만 안다. 속도는 모른다 — 그래서 속도 분산을 크게 둔다.
        self.P = np.diag([MEAS_NOISE ** 2, MEAS_NOISE ** 2, 4.0, 4.0])

    def predict(self, dt: float) -> None:
        if dt <= 0.0:
            return
        F = np.array([[1.0, 0.0, dt, 0.0],
                      [0.0, 1.0, 0.0, dt],
                      [0.0, 0.0, 1.0, 0.0],
                      [0.0, 0.0, 0.0, 1.0]])
        q = PROCESS_NOISE
        d2, d3, d4 = dt * dt, dt ** 3, dt ** 4
        Q = q * np.array([[d4 / 4, 0.0, d3 / 2, 0.0],
                          [0.0, d4 / 4, 0.0, d3 / 2],
                          [d3 / 2, 0.0, d2, 0.0],
                          [0.0, d3 / 2, 0.0, d2]])
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q

    def update(self, pos: tuple[float, float]) -> None:
        H = np.array([[1.0, 0.0, 0.0, 0.0],
                      [0.0, 1.0, 0.0, 0.0]])
        R = np.eye(2) * (MEAS_NOISE ** 2)
        z = np.array([pos[0], pos[1]], dtype=float)
        y = z - H @ self.x
        S = H @ self.P @ H.T + R
        try:
            K = self.P @ H.T @ np.linalg.inv(S)
        except np.linalg.LinAlgError:            # 수치가 무너지면 측정으로 리셋한다
            self.x = np.array([pos[0], pos[1], 0.0, 0.0], dtype=float)
            self.P = np.diag([MEAS_NOISE ** 2, MEAS_NOISE ** 2, 4.0, 4.0])
            return
        self.x = self.x + K @ y
        self.P = (np.eye(4) - K @ H) @ self.P

    @property
    def pos(self) -> tuple[float, float]:
        return (float(self.x[0]), float(self.x[1]))

    @property
    def vel(self) -> tuple[float, float]:
        return (float(self.x[2]), float(self.x[3]))

    @property
    def speed(self) -> float:
        return math.hypot(self.x[2], self.x[3])


@dataclass
class Sample:
    """측정이 들어온 순간의 기록. 예측만 한 틱은 남기지 않는다.

    급정지·전도는 '측정이 그렇게 변했다' 로만 말할 수 있다. 예측값을 섞어 넣으면
    필터가 만든 매끄러운 곡선에서 충격을 찾게 되고, 그때는 이미 사라진 뒤다.
    """

    t: float
    speed: float
    aspect: float                    # 박스 w/h. 전도하면 이 값이 뒤집힌다


@dataclass
class Track:
    """한 물체의 시계열."""

    track_id: int
    kind: str                        # AMR | PERSON
    box: dict                        # 마지막으로 본 정규화 박스 (그대로 발행에 쓴다)
    kalman: Kalman2D
    first_seen: float
    last_seen: float                 # 마지막 **측정** 시각 (도착 시각 기준)
    hits: int = 1
    heading: tuple[float, float] = (0.0, 0.0)   # 마지막으로 의미 있던 진행 방향
    samples: list[Sample] = field(default_factory=list)

    @property
    def pos(self) -> tuple[float, float]:
        return self.kalman.pos

    @property
    def vel(self) -> tuple[float, float]:
        return self.kalman.vel

    @property
    def speed(self) -> float:
        return self.kalman.speed

    @property
    def aspect(self) -> float:
        return _aspect(self.box)

    def label(self) -> str:
        return str(self.box.get("label") or ("AMR" if self.kind == AMR else "사람"))

    def score(self) -> float:
        try:
            return float(self.box.get("score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def recent(self, now: float, window: float) -> list[Sample]:
        return [s for s in self.samples if now - s.t <= window]


def _aspect(box: dict) -> float:
    w = abs(float(box["x2"]) - float(box["x1"]))
    h = abs(float(box["y2"]) - float(box["y1"]))
    return w / h if h > 1e-6 else 0.0


def iou(a: dict, b: dict) -> float:
    ax1, ay1, ax2, ay2 = (float(a["x1"]), float(a["y1"]), float(a["x2"]), float(a["y2"]))
    bx1, by1, bx2, by2 = (float(b["x1"]), float(b["y1"]), float(b["x2"]), float(b["y2"]))
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    if inter <= 0.0:
        return 0.0
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 1e-12 else 0.0


class Tracker:
    """한 카메라의 한 종류(AMR 또는 사람)를 맡는다.

    종류를 섞지 않는 이유: 사람과 AMR 이 겹쳐 보일 때 IoU 로 엮으면 ID 가 서로 넘어간다.
    그러면 '사람이 갑자기 AMR 속도로 움직였다' 가 되어 충돌 판정이 통째로 망가진다.
    """

    def __init__(self, kind: str, *, iou_gate: float = 0.2, max_age: float = 1.5,
                 min_hits: int = 2, history_sec: float = 3.0) -> None:
        self.kind = kind
        self.iou_gate = iou_gate
        self.max_age = max_age
        self.min_hits = min_hits
        self.history_sec = history_sec
        self.tracks: dict[int, Track] = {}
        self._next_id = 1
        self._last_predict = 0.0

    # ── 진행 ────────────────────────────────────────────────────────

    def predict(self, now: float) -> None:
        """측정이 없어도 시간은 간다. 가림 순간에도 트랙이 계속 움직여야 한다."""
        if self._last_predict <= 0.0:
            self._last_predict = now
            return
        dt = now - self._last_predict
        self._last_predict = now
        if dt <= 0.0:
            return
        for tr in self.tracks.values():
            tr.kalman.predict(dt)

    def update(self, now: float, dets: list[tuple[dict, tuple[float, float]]]) -> None:
        """`dets` 는 (정규화 박스, 월드 좌표) 쌍. 월드 좌표는 호모그래피를 지난 값이다.

        **`predict(now)` 를 먼저 부른 뒤에 같은 `now` 로 부른다.** 여기서 또 예측하면
        같은 시간이 두 번 흐른 것이 되어 속도 추정이 절반으로 줄어든다.

        탐욕적 IoU 매칭이다. 헝가리안을 쓰지 않는 이유: 한 프레임에 같은 종류가 수십
        개씩 보이는 현장이 아니고(AMR 몇 대, 사람 몇 명), 그 규모에서 두 방법의 결과는
        거의 같다. 의존성을 하나 더 들이는 값을 못 한다.
        """
        pairs: list[tuple[float, int, int]] = []
        for di, (box, _) in enumerate(dets):
            for tid, tr in self.tracks.items():
                score = iou(tr.box, box)
                if score >= self.iou_gate:
                    pairs.append((score, di, tid))
        pairs.sort(reverse=True)

        used_det: set[int] = set()
        used_trk: set[int] = set()
        for _, di, tid in pairs:
            if di in used_det or tid in used_trk:
                continue
            used_det.add(di)
            used_trk.add(tid)
            self._absorb(self.tracks[tid], now, *dets[di])

        for di, (box, world) in enumerate(dets):
            if di in used_det:
                continue
            tr = Track(track_id=self._next_id, kind=self.kind, box=box,
                       kalman=Kalman2D(world), first_seen=now, last_seen=now)
            tr.samples.append(Sample(now, 0.0, _aspect(box)))
            self.tracks[self._next_id] = tr
            self._next_id += 1

    def expire(self, now: float) -> list[Track]:
        """오래 못 본 트랙을 버린다. 버린 것을 돌려준다 — 판정 쪽이 정리할 것이 있다."""
        gone = [tr for tr in self.tracks.values() if now - tr.last_seen > self.max_age]
        for tr in gone:
            self.tracks.pop(tr.track_id, None)
        return gone

    def confirmed(self) -> list[Track]:
        """한 프레임 반짝한 오탐으로 알람을 내지 않기 위해, 두 번 이상 본 것만 쓴다."""
        return [t for t in self.tracks.values() if t.hits >= self.min_hits]

    # ── 내부 ────────────────────────────────────────────────────────

    def _absorb(self, tr: Track, now: float, box: dict,
                world: tuple[float, float]) -> None:
        # 시간을 흘리는 것은 predict() 의 일이다. 여기서는 측정만 반영한다.
        tr.kalman.update(world)
        tr.box = box
        tr.hits += 1
        tr.last_seen = now
        if tr.kalman.speed > 0.05:
            # 서 있는 동안의 방향은 잡음이다. 마지막으로 '움직였을 때' 의 방향을 붙든다 —
            # 스테이션 앞에 잠깐 선 AMR 의 통로는 여전히 진행하던 쪽이다.
            tr.heading = tr.kalman.vel
        tr.samples.append(Sample(now, tr.kalman.speed, _aspect(box)))
        cutoff = now - self.history_sec
        if tr.samples[0].t < cutoff:
            tr.samples = [s for s in tr.samples if s.t >= cutoff]

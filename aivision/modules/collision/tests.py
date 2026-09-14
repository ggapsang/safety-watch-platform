"""모듈 자체 검증 — 브로커도 플랫폼도 카메라도 없이 돈다.

충돌 모듈에서 이 파일이 특히 중요한 이유: 이 모듈은 **사고가 나야 결과가 보이는** 물건이다.
현장에서 확인하려면 진짜로 사람과 AMR 을 부딪치게 해야 하는데 그럴 수는 없다. 그래서
시나리오를 여기서 만들어 돌린다 — 접근하는 AMR, 멈춰 선 사람, 급정지, 전도.

여기서 확인하는 것.
  1. 좌표 변환    정규화 이미지 4점 -> 바닥 평면. 못 푸는 4점은 거절하는가
  2. 위험 기하    전방 스윕과 통로 판정이 앞뒤·좌우를 구분하는가
  3. 트래킹       ID 없는 박스에서 시계열이 생기는가. 속도가 맞는가
  4. 위험 알람    접근 시나리오에서 주의·임박이 제때 나오는가
  5. 충돌 판독    접촉만으로는 확정하지 않고, 접촉+충격에서 확정하는가
  6. 전이 발행    detect 가 전이 순간에만 나가는가 (머무는 동안 반복 없음)
  7. 계약 일치    우리 payload 를 플랫폼의 mapping 이 그대로 읽는가
  8. 설정 저장    화면에서 고친 값이 파일로 오가는가

실행: python tests.py
"""

from __future__ import annotations

import math
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # _sdk 가 옆에 있다

import config as config_module                                    # noqa: E402
import geometry                                                   # noqa: E402
import judge                                                      # noqa: E402
import settings as settings_module                                # noqa: E402
import tracking                                                   # noqa: E402
import zones                                                      # noqa: E402
from _sdk import Frame                                            # noqa: E402
from pipeline import CameraPipeline                               # noqa: E402
from tracking import AMR, PERSON, Kalman2D, Track                 # noqa: E402

CHECKS = 0

# 시험용 보정: 정규화 이미지 전체가 10m × 10m 바닥이다. 월드 좌표 = (10u, 10v).
CALIB = [(0.0, 0.0, 0.0, 0.0), (1.0, 0.0, 10.0, 0.0),
         (1.0, 1.0, 10.0, 10.0), (0.0, 1.0, 0.0, 10.0)]


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def setup_of(points=CALIB) -> settings_module.CameraSetup:
    return settings_module.CameraSetup(
        points=[settings_module.CalibPoint(u=u, v=v, x=x, y=y)
                for u, v, x, y in points])


def box_at(world_x: float, world_y: float, *, w: float = 0.06, h: float = 0.12,
           label: str = "Human", score: float = 0.9) -> dict:
    """월드 좌표에 서 있는 물체의 정규화 박스. 발 접지점이 그 자리에 오도록 만든다."""
    u, v = world_x / 10.0, world_y / 10.0
    return {"x1": round(u - w / 2, 6), "y1": round(v - h, 6),
            "x2": round(u + w / 2, 6), "y2": round(v, 6),
            "label": label, "score": score}


def make_track(kind: str, tid: int, pos, vel, *, box: dict | None = None) -> Track:
    tr = Track(track_id=tid, kind=kind,
               box=box or box_at(pos[0], pos[1],
                                 label="Human" if kind == PERSON else "자율주행로봇"),
               kalman=Kalman2D(pos), first_seen=0.0, last_seen=0.0, hits=5)
    tr.kalman.x[2] = vel[0]
    tr.kalman.x[3] = vel[1]
    tr.heading = vel
    return tr


# ────────────────────────────────────────────────── 1. 좌표 변환

def test_homography() -> None:
    print("좌표 변환")
    pairs = [((u, v), (x, y)) for u, v, x, y in CALIB]
    H = geometry.homography_from_points(pairs)
    check(H is not None, "4점에서 호모그래피가 나온다")

    got = geometry.project(H, 0.5, 0.5)
    check(abs(got[0] - 5.0) < 1e-6 and abs(got[1] - 5.0) < 1e-6, "가운데가 (5m, 5m)")
    check(geometry.reprojection_error(H, pairs) < 1e-6, "보정 점의 재투영 오차가 0")

    # 원근이 섞인 사다리꼴도 풀려야 한다 (실제 카메라는 이 모양이다)
    trapezoid = [((0.30, 0.40), (0.0, 8.0)), ((0.70, 0.40), (4.0, 8.0)),
                 ((0.95, 0.95), (4.0, 0.0)), ((0.05, 0.95), (0.0, 0.0))]
    H2 = geometry.homography_from_points(trapezoid)
    check(H2 is not None and geometry.reprojection_error(H2, trapezoid) < 1e-4,
          "사다리꼴(원근) 대응도 정확히 풀린다")

    # 한 줄에 놓인 4점은 평면을 정하지 못한다. 숫자를 내놓느니 거절해야 한다.
    line = [((0.1, 0.5), (1.0, 5.0)), ((0.2, 0.5), (2.0, 5.0)),
            ((0.3, 0.5), (3.0, 5.0)), ((0.4, 0.5), (4.0, 5.0))]
    check(geometry.homography_from_points(line) is None, "한 줄에 놓인 4점은 거절한다")
    check(geometry.homography_from_points(pairs[:3]) is None, "3점은 거절한다")

    # 박스 하단 중앙이 발 접지점이다
    u, v = geometry.foot_point({"x1": 0.2, "y1": 0.1, "x2": 0.4, "y2": 0.6})
    check(abs(u - 0.3) < 1e-9 and abs(v - 0.6) < 1e-9, "발 접지점은 하단 중앙")


# ────────────────────────────────────────────────── 2. 위험 기하

def test_sweep_and_corridor() -> None:
    print("위험 기하")

    # AMR 이 원점에서 +x 로 1m/s. 사람은 앞 5m.
    tau = geometry.sweep_tau((0.0, 0.0), (1.0, 0.0), (5.0, 0.0),
                             radius=1.0, growth=0.0, horizon=10.0)
    check(tau is not None and abs(tau - 4.0) < 1e-6, "정면 접근 τ = (거리-반경)/속도")

    # 사람이 어느 방향으로든 움직일 수 있다고 보면(growth) 더 일찍 닿는다
    tau2 = geometry.sweep_tau((0.0, 0.0), (1.0, 0.0), (5.0, 0.0),
                              radius=1.0, growth=1.0, horizon=10.0)
    check(tau2 is not None and tau2 < tau, "사람 도달 반경이 커지면 τ 가 당겨진다")

    # 멀어지는 중이면 스윕은 닿지 않는다
    check(geometry.sweep_tau((0.0, 0.0), (-1.0, 0.0), (5.0, 0.0),
                             radius=1.0, growth=0.0, horizon=10.0) is None,
          "멀어지는 AMR 은 닿지 않는다")
    # 전망 시간 밖이면 없는 것과 같다
    check(geometry.sweep_tau((0.0, 0.0), (1.0, 0.0), (50.0, 0.0),
                             radius=1.0, growth=0.0, horizon=2.0) is None,
          "전망 시간(horizon) 밖은 임박이 아니다")
    # 이미 겹쳐 있으면 0
    check(geometry.sweep_tau((0.0, 0.0), (1.0, 0.0), (0.5, 0.0),
                             radius=1.0, growth=0.0, horizon=2.0) == 0.0,
          "이미 반경 안이면 τ = 0")

    # 통로는 앞뒤를 구분한다 — 이것이 원형 거리와 다른 점이다
    inside, ahead, lateral = geometry.corridor_hit(
        (0.0, 0.0), (1.0, 0.0), (2.0, 0.2), half_w=1.0, length=3.0)
    check(inside and abs(ahead - 2.0) < 1e-9 and abs(lateral - 0.2) < 1e-9,
          "앞 2m·옆 0.2m 는 통로 안")
    behind, ahead_b, _ = geometry.corridor_hit(
        (0.0, 0.0), (1.0, 0.0), (-2.0, 0.0), half_w=1.0, length=3.0)
    check(not behind and ahead_b < 0, "뒤쪽 2m 는 통로 밖")
    side, _, _ = geometry.corridor_hit(
        (0.0, 0.0), (1.0, 0.0), (2.0, 3.0), half_w=1.0, length=3.0)
    check(not side, "옆으로 3m 벗어나면 통로 밖")

    t_cpa, d_cpa = geometry.closest_approach((5.0, 1.0), (-1.0, 0.0))
    check(abs(t_cpa - 5.0) < 1e-9 and abs(d_cpa - 1.0) < 1e-9, "최근접 시각과 거리")
    t_away, d_away = geometry.closest_approach((5.0, 0.0), (1.0, 0.0))
    check(t_away == 0.0 and abs(d_away - 5.0) < 1e-9, "멀어지는 중이면 지금이 최근접")


# ────────────────────────────────────────────────── 3. 트래킹

def test_tracking() -> None:
    print("트래킹")
    trk = tracking.Tracker(PERSON, max_age=1.0)

    # 등속으로 걸어가는 사람. 박스에 ID 가 없어도 같은 트랙으로 이어져야 한다.
    t = 0.0
    for i in range(10):
        t = i * 0.2
        x = 1.0 + 0.5 * t                      # 0.5 m/s
        trk.predict(t)
        trk.update(t, [(box_at(x, 5.0), (x, 5.0))])
    tracks = trk.confirmed()
    check(len(tracks) == 1, "한 사람이 한 트랙으로 이어진다")
    tr = tracks[0]
    check(tr.track_id == 1 and tr.hits == 10, "ID 가 유지된다")
    check(abs(tr.speed - 0.5) < 0.1, f"속도가 0.5m/s 로 수렴한다 (측정 {tr.speed:.3f})")
    check(abs(tr.pos[0] - (1.0 + 0.5 * t)) < 0.2, "위치가 따라간다")

    # 떨어진 자리의 박스는 새 트랙이다
    trk.predict(t + 0.2)
    trk.update(t + 0.2, [(box_at(1.0 + 0.5 * (t + 0.2), 5.0), (1.0 + 0.5 * (t + 0.2), 5.0)),
                         (box_at(9.0, 9.0), (9.0, 9.0))])
    check(len(trk.tracks) == 2, "떨어진 박스는 새 트랙")
    check(len(trk.confirmed()) == 1, "한 번만 본 트랙은 아직 판정에 쓰지 않는다")

    # 짧은 미검출은 견디고, 오래 못 보면 버린다
    trk.predict(t + 0.8)
    check(trk.expire(t + 0.8) == [], "0.6초 미검출은 견딘다 (가림)")
    trk.predict(t + 2.5)
    gone = trk.expire(t + 2.5)
    check(len(gone) == 2 and trk.tracks == {}, "max_age 를 넘기면 버린다")

    # 예측만으로도 트랙은 계속 움직인다 (가림 구간)
    k = Kalman2D((0.0, 0.0))
    for i in range(20):
        k.predict(0.1)
        k.update((0.1 * (i + 1), 0.0))
    before = k.pos[0]
    k.predict(0.5)
    check(k.pos[0] > before, "측정이 없어도 예측으로 나아간다")
    check(abs(k.speed - 1.0) < 0.15, f"등속 1m/s 추정 (측정 {k.speed:.3f})")


# ────────────────────────────────────────────────── 4. 위험 알람

def test_risk() -> None:
    print("위험 알람")
    tun = settings_module.Thresholds()

    # 멀리 있는 사람은 아무것도 아니다
    amr = make_track(AMR, 1, (1.0, 5.0), (1.0, 0.0))
    far = make_track(PERSON, 2, (9.5, 9.5), (0.0, 0.0))
    check(judge.assess([far], [amr], tun)[0].level == "", "멀리 있으면 위험이 아니다")

    # 통로 안이지만 아직 스윕이 못 닿는 거리 -> 주의
    warn_amr = make_track(AMR, 3, (0.0, 5.0), (1.5, 0.0))
    warn_person = make_track(PERSON, 4, (6.0, 5.0), (0.0, 0.0))
    p = judge.assess([warn_person], [warn_amr], tun)[0]
    check(p.level == judge.WARN, f"통로 안·먼 거리는 주의 (level={p.level})")

    # 같은 통로에서 가까워지면 임박으로 올라간다
    near_person = make_track(PERSON, 5, (2.5, 5.0), (0.0, 0.0))
    p2 = judge.assess([near_person], [warn_amr], tun)[0]
    check(p2.level == judge.IMMINENT, f"전방 근접은 임박 (level={p2.level})")
    check(p2.tau is not None and p2.tau <= tun.t_imminent, "임박에는 τ 가 붙는다")
    check(p2.closing > 0, "접근 중이면 closing 이 양수")

    # AMR 뒤쪽은 위험이 아니다. 원형 거리만 보면 구분되지 않는 자리다.
    # (다만 r_amr+r_h+σ 안쪽은 방향과 무관하게 임박이다 — 그만큼 붙어 있으면 위험하다.)
    behind_amr = make_track(AMR, 6, (3.0, 5.0), (1.0, 0.0))
    behind = make_track(PERSON, 7, (0.0, 5.0), (0.0, 0.0))
    check(judge.assess([behind], [behind_amr], tun)[0].level == "",
          "AMR 뒤쪽 3m 는 위험이 아니다")

    # 서 있는 AMR 은 임박을 내지 않는다 — 쓸 속도가 없다. 대신 통로 주의로 남는다.
    stopped = make_track(AMR, 8, (1.0, 5.0), (0.0, 0.0))
    stopped.heading = (1.0, 0.0)
    p3 = judge.assess([make_track(PERSON, 9, (2.5, 5.0), (0.0, 0.0))], [stopped], tun)[0]
    check(p3.level == judge.WARN, f"서 있는 AMR 앞은 주의까지 (level={p3.level})")

    # 옆 통로를 지나가는 AMR — 통로 반폭 밖이고 스윕도 못 닿으면 위험이 아니다
    side = make_track(PERSON, 10, (2.0, 8.0), (0.0, 0.0))
    check(judge.assess([side], [amr], tun)[0].level == "", "통로 옆으로 3m 는 위험이 아니다")


# ────────────────────────────────────────────────── 5. 충돌 판독

def _feed_samples(tr: Track, speeds: list[tuple[float, float]],
                  aspect: float = 0.4) -> None:
    for t, speed in speeds:
        tr.samples.append(tracking.Sample(t, speed, aspect))


def test_collision() -> None:
    print("충돌 판독")
    tun = settings_module.Thresholds()

    # (1) 접촉만 — 확정하지 않는다. 박스가 겹치는 일은 하루에도 수없이 있다.
    watcher = judge.CollisionWatcher(1)
    amr = make_track(AMR, 1, (5.0, 5.0), (0.6, 0.0))
    person = make_track(PERSON, 2, (5.3, 5.0), (0.0, 0.0))
    _feed_samples(amr, [(9.0, 0.6), (9.3, 0.6), (9.6, 0.6)])
    _feed_samples(person, [(9.0, 0.0), (9.3, 0.0), (9.6, 0.0)])
    pairs = judge.assess([person], [amr], tun)
    check(pairs[0].dist <= tun.eps_contact, "0.3m 는 접촉 거리 안")
    check(watcher.feed(10.0, pairs, tun) == [], "접촉만으로는 확정하지 않는다")

    # (2) 접촉 + AMR 급정지 -> 확정
    watcher2 = judge.CollisionWatcher(1)
    amr2 = make_track(AMR, 3, (5.0, 5.0), (0.05, 0.0))
    person2 = make_track(PERSON, 4, (5.3, 5.0), (0.0, 0.0))
    _feed_samples(amr2, [(9.4, 1.2), (9.7, 1.1), (10.0, 0.05)])   # 급정지
    _feed_samples(person2, [(9.4, 0.0), (9.7, 0.0), (10.0, 0.0)])
    pairs2 = judge.assess([person2], [amr2], tun)
    confs = watcher2.feed(10.0, pairs2, tun)
    check(len(confs) == 1, "접촉 + 급정지 = 확정")
    check(confs[0].impacts[0].kind == "amr_stop", "근거가 AMR 급정지로 남는다")
    check(watcher2.feed(10.1, pairs2, tun) == [], "같은 에피소드에서 두 번 확정하지 않는다")
    check(len(watcher2.live()) == 1, "확정 상태가 유지된다 (해제는 Debouncer 가 한다)")

    # (3) 전도 — 종횡비가 뒤집히면 등급이 스침으로 내려가지 않는다
    watcher3 = judge.CollisionWatcher(1)
    amr3 = make_track(AMR, 5, (5.0, 5.0), (0.1, 0.0))
    person3 = make_track(PERSON, 6, (5.25, 5.0), (0.0, 0.0))
    _feed_samples(amr3, [(9.4, 0.3), (9.7, 0.3), (10.0, 0.28)])
    for t, aspect in ((9.2, 0.4), (9.5, 0.42), (9.8, 0.41), (10.0, 1.3)):
        person3.samples.append(tracking.Sample(t, 0.1, aspect))
    person3.box = dict(person3.box, x1=0.40, x2=0.60, y1=0.47, y2=0.52)
    pairs3 = judge.assess([person3], [amr3], tun)
    confs3 = watcher3.feed(10.0, pairs3, tun)
    check(len(confs3) == 1 and any(i.kind == "topple" for i in confs3[0].impacts),
          "전도(종횡비 반전)를 충격으로 본다")
    check(confs3[0].grade != judge.GRAZE, "전도는 '접촉·스침' 으로 기록되지 않는다")

    # (4) 충격이 Δt 창 밖이면 확정하지 않는다 — 우연히 지나가다 멈춘 것과 구별해야 한다
    watcher4 = judge.CollisionWatcher(1)
    amr4 = make_track(AMR, 7, (5.0, 5.0), (0.05, 0.0))
    person4 = make_track(PERSON, 8, (5.3, 5.0), (0.0, 0.0))
    _feed_samples(amr4, [(1.0, 1.2), (1.2, 0.05)])          # 한참 전에 멈췄다
    _feed_samples(person4, [(9.9, 0.0), (10.0, 0.0)])
    check(watcher4.feed(10.0, judge.assess([person4], [amr4], tun), tun) == [],
          "Δt 창 밖의 충격은 확정 근거가 아니다")

    # (5) 등급 — 상대속도로 가른다
    check(judge._grade(0.2, [], tun) == judge.GRAZE, "느린 접촉은 스침")
    check(judge._grade(0.8, [], tun) == judge.KNOCKDOWN, "중간 속도는 충돌·전도")
    check(judge._grade(2.0, [], tun) == judge.SEVERE, "빠른 충돌은 심각")


# ────────────────────────────────────────────────── 6. 전이 발행

class FakePublisher:
    """발행된 것을 토픽째 적어 둔다. 무엇을 어느 토픽으로 냈는지가 계약의 절반이다."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.cleared: list[tuple[int, list]] = []      # publish_live() (화면 비우기)

    def publish(self, topic: str, payload: dict, qos: int = 0) -> None:
        self.sent.append((topic, payload))

    def publish_live(self, module_id: str, camera_id: int, boxes: list) -> None:
        self.cleared.append((camera_id, boxes))

    @property
    def detect(self) -> list[tuple[str, dict]]:
        return [(t, p) for t, p in self.sent if t.startswith("aivision/detect/")]

    @property
    def live_raw(self) -> list[tuple[str, dict]]:
        return [(t, p) for t, p in self.sent if t.startswith("aivision/live/")]


def make_cfg(tmp: str) -> config_module.Config:
    cfg = config_module.Config()
    cfg.config_dir = tmp
    cfg.amr_labels = ["자율주행로봇"]
    cfg.person_labels = ["Human"]
    cfg.risk_item = "COLLISION_RISK"
    cfg.collision_item = "COLLISION"
    cfg.evidence_snapshot = False
    return cfg


def test_transitions() -> None:
    """계약에서 가장 중요한 부분 — detect 는 **바뀌는 순간만** 나간다(계약 1.3)."""
    print("전이 발행")
    pub = FakePublisher()
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_cfg(tmp)
        pipe = CameraPipeline(1, cfg, pub, setup_of())

        # 사람은 (6,5) 에 서 있고, AMR 이 +x 로 다가온다.
        t = 0.0
        for i in range(25):
            t = i * 0.2
            x = 1.0 + 0.8 * t
            boxes = [box_at(6.0, 5.0, label="Human"),
                     box_at(x, 5.0, w=0.10, h=0.08, label="자율주행로봇")]
            pipe.offer(Frame(camera_id=1, module_id="yolo-server", ts="", boxes=boxes,
                             received=t))
            pipe.tick(t)

        risk = [p for _, p in pub.detect if p["item"] == "COLLISION_RISK"]
        check(len(risk) >= 1, "접근하면 COLLISION_RISK 가 나간다")
        check(risk[0]["state"] == "active", "첫 발행은 발생(active)")
        check(risk[0]["level"] in (judge.WARN, judge.IMMINENT),
              "payload 에 level 이 실린다")
        actives = [p for p in risk if p["state"] == "active"]
        check(len(actives) <= 2,
              f"머무는 동안 반복해 내지 않는다 (발행 {len(actives)}건: 발생 + 임박 승격)")
        if len(actives) == 2:
            check(actives[0]["level"] == judge.WARN
                  and actives[1]["level"] == judge.IMMINENT,
                  "두 번째 발행은 주의 -> 임박 승격")

        # 계약 2장: 좌표는 0~1, 라벨은 사람이 읽을 이름
        for b in risk[0]["boxes"]:
            check(all(0.0 <= b[k] <= 1.0 for k in ("x1", "y1", "x2", "y2")),
                  "이벤트 박스 좌표가 0~1")
        check({b["label"] for b in risk[0]["boxes"]} == {"Human", "자율주행로봇"},
              "박스 라벨이 사람이 읽을 이름")

        # 사람이 사라지면 hold_sec 뒤에 해제가 나간다
        before = len(pub.detect)
        for i in range(40):
            t += 0.2
            pipe.offer(Frame(camera_id=1, module_id="yolo-server", ts="",
                             boxes=[box_at(9.5, 0.5, w=0.10, h=0.08,
                                           label="자율주행로봇")], received=t))
            pipe.tick(t)
        released = [p for _, p in pub.detect[before:] if p["state"] == "inactive"]
        check(len(released) == 1, "해제는 한 번만 나간다")
        check(released[0]["item"] == "COLLISION_RISK", "해제된 항목이 맞다")
        check(released[0]["level"] == actives[-1]["level"],
              "해제에는 끝난 알람의 수준이 실린다 (임박이었으면 임박으로 끝난다)")

        # 토픽 모양 (계약 1.3)
        topic = pub.detect[0][0]
        check(topic == "aivision/detect/1/collision", f"토픽 모양 ({topic})")


def test_uncalibrated_and_disabled() -> None:
    """보정 전에도 죽지 않는다. 다만 거리를 못 재므로 판정하지 않는다."""
    print("보정 전·꺼 둔 카메라")
    pub = FakePublisher()
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_cfg(tmp)
        pipe = CameraPipeline(2, cfg, pub, settings_module.CameraSetup())
        for i in range(10):
            t = i * 0.2
            pipe.offer(Frame(camera_id=2, module_id="yolo-server", ts="",
                             boxes=[box_at(6.0, 5.0, label="Human"),
                                    box_at(5.8, 5.0, label="자율주행로봇")], received=t))
            pipe.tick(t)
        check(pub.detect == [], "미보정 카메라는 이벤트를 내지 않는다")
        check(pipe.status()["received"] == 10, "그래도 입력은 받고 있다 (화면이 안다)")
        check(pipe.status()["calibrated"] is False, "화면에 미보정으로 보인다")

        # 꺼 둔 카메라도 마찬가지다
        off = setup_of()
        off.enabled = False
        pipe2 = CameraPipeline(3, cfg, pub, off)
        for i in range(10):
            t = i * 0.2
            pipe2.offer(Frame(camera_id=3, module_id="yolo-server", ts="",
                              boxes=[box_at(6.0, 5.0, label="Human"),
                                     box_at(5.8, 5.0, label="자율주행로봇")], received=t))
            pipe2.tick(t)
        check(pub.detect == [], "꺼 둔 카메라도 이벤트를 내지 않는다")


def test_garbage_input() -> None:
    """남의 버그로 죽지 않는다(계약 3장)."""
    print("이상한 입력")
    pub = FakePublisher()
    with tempfile.TemporaryDirectory() as tmp:
        pipe = CameraPipeline(1, make_cfg(tmp), pub, setup_of())
        junk = [
            {"x1": 0.1, "y1": 0.2, "label": "Human"},               # 좌표가 모자라다
            {"x1": "a", "y1": 0.2, "x2": 0.3, "y2": 0.4, "label": "Human"},
            {"x1": 0.1, "y1": 0.2, "x2": 0.3, "y2": 0.4},           # 라벨이 없다
            {"x1": 0.1, "y1": 0.2, "x2": 0.3, "y2": 0.4, "label": "고양이"},
        ]
        pipe.offer(Frame(camera_id=1, module_id="yolo-server", ts="", boxes=junk,
                         received=0.1))
        pipe.tick(0.1)
        check(pub.detect == [], "모양이 다른 박스는 버리고 조용히 넘어간다")
        check(pipe.status()["tracks"] == {"amr": 0, "person": 0}, "트랙이 생기지 않는다")


# ────────────────────────────────────────────────── 7. 계약 일치

def test_platform_contract() -> None:
    """우리가 발행하는 payload 를 플랫폼의 mapping 이 그대로 읽는지 본다.

    같은 저장소에 있어도 서로 다른 프로세스라 계약이 어긋나도 조용하다. 조용히 어긋나는
    것을 여기서 잡는다. `level`·`grade` 를 더해도 기본 표현식이 깨지지 않아야 한다.
    """
    print("플랫폼 계약 일치")
    server = Path(__file__).resolve().parents[2] / "server"
    if not (server / "aivision_server" / "mqtt" / "mapping.py").is_file():
        print("  건너뜀 - 플랫폼 소스를 찾을 수 없습니다")
        return
    sys.path.insert(0, str(server))
    from aivision_server.mqtt import mapping

    pub = FakePublisher()
    with tempfile.TemporaryDirectory() as tmp:
        pipe = CameraPipeline(7, make_cfg(tmp), pub, setup_of())
        pipe._risk_level = judge.IMMINENT
        pipe._publish("COLLISION_RISK", "active", 0.884,
                      [box_at(6.0, 5.0, label="Human")], {"level": judge.IMMINENT})
    topic, payload = pub.detect[0]
    flat = mapping.preprocess(payload, "raw")

    check(mapping.topic_matches("aivision/detect/#", topic), "탐지 토픽이 프리셋 패턴에 걸린다")
    check(mapping.evaluate("$.camera_id", topic=topic, payload=flat) == 7, "카메라 표현식")
    check(mapping.evaluate("$.item", topic=topic, payload=flat) == "COLLISION_RISK",
          "항목 표현식")
    check(mapping.evaluate("$.state", topic=topic, payload=flat) == "active", "상태 표현식")
    check(abs(mapping.to_float(mapping.evaluate("$.confidence", topic=topic,
                                                payload=flat)) - 0.884) < 1e-9,
          "신뢰도가 0~1 그대로")
    check(mapping.to_time(mapping.evaluate("$.ts", topic=topic, payload=flat)) is not None,
          "시각 표현식")
    boxes = mapping.to_boxes(mapping.evaluate("$.boxes", topic=topic, payload=flat),
                             "xyxy_norm")
    check(len(boxes) == 1 and boxes[0]["label"] == "Human", "박스가 그대로 읽힌다")
    check(mapping.evaluate("$.level", topic=topic, payload=flat) == judge.IMMINENT,
          "덧붙인 level 도 표현식으로 읽힌다")


def test_live_roundtrip() -> None:
    """우리가 내는 라이브 오버레이를 SDK 의 `parse_frame` 이 읽는가.

    남이 낸 박스를 먹는 모듈이니, 우리가 내는 것도 남이 먹을 수 있어야 공평하다.
    """
    print("라이브 왕복")
    import json

    from _sdk import parse_frame

    pub = FakePublisher()
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_cfg(tmp)
        cfg.publish_live = True
        cfg.live_min_interval = 0.0
        pipe = CameraPipeline(4, cfg, pub, setup_of())
        for i in range(6):
            t = i * 0.2
            pipe.offer(Frame(camera_id=4, module_id="yolo-server", ts="",
                             boxes=[box_at(6.0, 5.0, label="Human"),
                                    box_at(3.0, 5.0, w=0.1, h=0.08,
                                           label="자율주행로봇")], received=t))
            pipe.tick(t)
    check(pub.live_raw, "라이브 오버레이가 나간다")
    _, payload = pub.live_raw[-1]
    frame = parse_frame("aivision/live/4",
                        json.dumps(payload, ensure_ascii=False).encode())
    check(frame is not None and len(frame.boxes) == len(payload["boxes"]),
          "우리 라이브 박스를 parse_frame 이 그대로 읽는다")


# ────────────────────────────────────────────────── 8. 설정 저장

def test_settings_store() -> None:
    print("모듈 설정")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        check(settings_module.load(root).cameras == {}, "파일이 없으면 빈 설정")

        s = settings_module.Settings()
        s.amr_labels = ["자율주행로봇"]
        s.person_labels = ["Human", "사람"]
        s.risk_item = "COLLISION_RISK"
        s.collision_item = "COLLISION"
        s.cameras[1] = setup_of()
        s.cameras[1].note = "A동 1번 통로"

        # 임계값은 범위 밖이어도 거절하지 않고 자른다
        s.set_tuning({"eps_contact": 99.0, "v_h_max": -1.0, "t_imminent": 1.0})
        check(s.tuning["eps_contact"] == settings_module.TUNING["eps_contact"][1],
              "위로 넘치면 최댓값으로 자른다")
        check(s.tuning["v_h_max"] == settings_module.TUNING["v_h_max"][0],
              "아래로 넘치면 최솟값으로 자른다")
        check(s.tuning["t_imminent"] == 1.0, "범위 안이면 그대로")
        check(s.set_tuning({"없는값": 1.0}) is not None
              and "없는값" not in s.tuning, "모르는 이름은 들이지 않는다")

        settings_module.save(root, s)
        again = settings_module.load(root)
        check(again.person_labels == ["Human", "사람"], "라벨이 남는다")
        check(again.risk_item == "COLLISION_RISK", "항목 매핑이 남는다")
        check(again.tuning["t_imminent"] == 1.0, "임계값이 남는다")
        check(len(again.setup(1).points) == 4 and again.setup(1).calibrated,
              "보정 4점이 남고 다시 풀린다")
        check(again.setup(1).note == "A동 1번 통로", "메모가 남는다")
        check(again.setup(9).calibrated is False, "보정 없는 카메라는 미보정")

        # 행렬이 아니라 점을 저장한다 — 둘 다 저장하면 언젠가 서로 어긋난다
        raw = (root / settings_module.FILENAME).read_text(encoding="utf-8")
        check("homography" not in raw, "파일에는 4점만 있고 유도된 행렬은 없다")

        # 깨진 파일에 기동이 막히면 안 된다
        (root / settings_module.FILENAME).write_text("{이건 JSON 이 아니다",
                                                     encoding="utf-8")
        check(settings_module.load(root).cameras == {}, "깨진 설정은 빈 설정으로 내려앉는다")


def test_settings_seeding() -> None:
    """화면에서 라벨만 저장해도 항목 연결이 풀리면 안 된다.

    실제로 났던 사고다. 파일이 없는 채로 라벨을 저장하면 설정 전체가 파일로 쓰이는데,
    그때 항목 코드가 빈 값으로 적혔다. 다음 기동에서 그 빈 값이 '사람이 비웠다' 로 읽혀
    이벤트가 조용히 끊긴다 — 아무 에러도 없이.
    """
    print("설정 씨앗")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = make_cfg(tmp)                       # env 기본: 항목·라벨이 채워져 있다

        s = settings_module.load(root)
        check(s.items_set is False, "파일이 없으면 '아직 정한 적 없음'")
        s.adopt(amr_labels=cfg.amr_labels, person_labels=cfg.person_labels,
                risk_item=cfg.risk_item, collision_item=cfg.collision_item)

        # 화면에서 라벨만 고쳐 저장한다
        s.person_labels = ["Human", "사람"]
        settings_module.save(root, s)

        again = settings_module.load(root)
        check(again.risk_item == "COLLISION_RISK", "라벨만 저장해도 항목 연결이 남는다")
        fresh = make_cfg(tmp)
        config_module.apply_settings(fresh, again)
        check(fresh.capabilities == ["COLLISION", "COLLISION_RISK"],
              "다음 기동에서도 같은 항목으로 등록한다")
        check(fresh.person_labels == ["Human", "사람"], "고친 라벨은 반영된다")

        # 사람이 **일부러** 비운 것은 존중해야 한다 — env 로 되살아나면 안 된다
        again.risk_item = ""
        again.collision_item = ""
        settings_module.save(root, again)
        cleared = make_cfg(tmp)
        config_module.apply_settings(cleared, settings_module.load(root))
        check(cleared.capabilities == [], "일부러 비운 항목은 env 로 되살아나지 않는다")


def test_zone_geometry() -> None:
    """그림이 판정의 설명인가.

    구역을 판정과 다른 숫자로 그리면, 사람은 구역 밖에 선 사람에게 울리는 알람을 보게
    되고 그 화면은 아무도 믿지 않는다. 그래서 '그린 사각형 안 = 주의' 를 못 박는다.
    """
    print("구역 기하")
    tun = settings_module.Thresholds()
    pairs = [((u, v), (x, y)) for u, v, x, y in CALIB]
    H = geometry.homography_from_points(pairs)
    H_inv = geometry.invert(H)
    check(H_inv is not None, "H⁻¹ 가 나온다")

    # 이미지 -> 월드 -> 이미지 왕복
    u, v = 0.42, 0.61
    x, y = geometry.project(H, u, v)
    back = geometry.to_image(H_inv, x, y)
    check(back is not None and abs(back[0] - u) < 1e-6 and abs(back[1] - v) < 1e-6,
          "이미지→월드→이미지 왕복이 제자리로 온다")

    amr = make_track(AMR, 1, (2.0, 5.0), (1.0, 0.0))
    heading, half_w, length, back_m = judge.corridor_of(amr, tun)
    quad = geometry.corridor_quad(amr.pos, heading, half_w, length, back_m)
    check(len(quad) == 4, "통로 네 꼭짓점")

    # 그린 사각형의 한가운데에 선 사람은 판정도 위험이어야 한다
    mid = (sum(p[0] for p in quad) / 4.0, sum(p[1] for p in quad) / 4.0)
    inside_person = make_track(PERSON, 2, mid, (0.0, 0.0))
    check(judge.assess([inside_person], [amr], tun)[0].level != "",
          "그린 통로 한가운데는 판정도 위험이다")

    # 통로 옆으로 반폭보다 더 벗어나면(그림 밖) 판정도 통로 밖이다
    outside = (mid[0], mid[1] + half_w + tun.v_h_max * tun.t_imminent + 1.0)
    out_person = make_track(PERSON, 3, outside, (0.0, 0.0))
    check(judge.assess([out_person], [amr], tun)[0].level == "",
          "그린 통로 밖(옆)은 판정도 위험이 아니다")

    # 사람 도달범위는 R_h(τ) 그대로여야 한다
    radius = judge.reach_radius(tun)
    expect = tun.r_h + tun.sigma + tun.v_h_max * tun.t_imminent
    check(abs(radius - expect) < 1e-9, "도달 반경 = r_h + σ + v_h,max·τ")

    circle = geometry.circle_points((5.0, 5.0), radius, steps=12)
    check(len(circle) == 12 and abs(math.hypot(circle[0][0] - 5.0,
                                               circle[0][1] - 5.0) - radius) < 1e-9,
          "원을 다각형으로 쪼갠다 (원근을 지나면 타원이 된다)")

    # 화면 밖으로 뻗은 구역도 계약 박스는 0~1 안에 있어야 한다
    far = geometry.project_polygon(H_inv, geometry.corridor_quad(
        (9.5, 5.0), (1.0, 0.0), 1.0, 50.0), (9.5, 5.0))
    box = geometry.bounding_box(far)
    check(box is None or all(0.0 <= c <= 1.0 for c in box),
          "화면을 넘어가는 구역도 박스는 0~1 로 잘린다")

    # 카메라 뒤로 넘어간 점은 버린다.
    #
    # 먼 점이 아니라 **분모의 부호가 뒤집히는 선 너머**가 문제다. 멀기만 한 점은 화면
    # 밖으로 나갈 뿐 부호가 그대로라 잘라 쓰면 되지만, 부호가 뒤집힌 점을 그대로 그리면
    # 구역이 화면 반대편으로 접혀 엉뚱한 사각형이 된다. 그 선을 직접 계산해 시험한다.
    tilted = [((0.10, 0.95), (0.0, 0.0)), ((0.90, 0.95), (4.0, 0.0)),
              ((0.62, 0.40), (4.0, 10.0)), ((0.38, 0.40), (0.0, 10.0))]
    H2_inv = geometry.invert(geometry.homography_from_points(tilted))
    h31, h32, h33 = H2_inv[2]
    check(abs(h32) > 1e-9, "이 보정에는 부호가 뒤집히는 선이 있다")
    y0 = -(h31 * 2.0 + h33) / h32                  # (2, y0) 에서 분모가 0 이다
    near = geometry.to_image(H2_inv, 2.0, y0 - 1.0)
    far = geometry.to_image(H2_inv, 2.0, y0 + 1.0)
    check(near is not None and far is not None and near[2] * far[2] < 0,
          "선을 사이에 두고 분모 부호가 갈린다")
    # 선을 가로지르는 가느다란 사각형 — 두 꼭짓점만 반대편에 있다.
    quad = [(2.0, y0 - 1.0), (2.05, y0 - 1.0), (2.05, y0 + 1.0), (2.0, y0 + 1.0)]
    anchor_w = geometry.to_image(H2_inv, 2.0, 1.0)[2]
    same_side = sum(1 for x, y in quad
                    if geometry.to_image(H2_inv, x, y)[2] * anchor_w > 0)
    check(same_side == 2, "네 꼭짓점 중 둘만 카메라 앞쪽이다")
    folded = geometry.project_polygon(H2_inv, quad, (2.0, 1.0))
    check(folded == [], f"뒤로 접히는 꼭짓점을 버리면 그릴 수 없다 (남은 점 {len(folded)})")


def test_zone_build() -> None:
    print("구역 만들기")
    tun = settings_module.Thresholds()
    H_inv = geometry.invert(geometry.homography_from_points(
        [((u, v), (x, y)) for u, v, x, y in CALIB]))

    amr = make_track(AMR, 1, (2.0, 5.0), (1.0, 0.0))
    person = make_track(PERSON, 2, (5.0, 5.0), (0.0, 0.0))
    pairs = judge.assess([person], [amr], tun)
    built = zones.build([person], [amr], pairs, tun, H_inv)

    kinds = {z.kind for z in built}
    check(kinds == {zones.CORRIDOR, zones.REACH}, "통로와 도달범위가 둘 다 나온다")

    corridor = next(z for z in built if z.kind == zones.CORRIDOR)
    reach = next(z for z in built if z.kind == zones.REACH)
    check(corridor.level == pairs[0].level, "구역 색(level)이 판정과 같다")
    check(corridor.world["length_m"] > 0 and reach.world["radius_m"] > 0,
          "미터 단위 원본이 함께 실린다")

    # 계약 2장 — 구역 박스도 박스다
    box = corridor.box()
    check(all(0.0 <= box[k] <= 1.0 for k in ("x1", "y1", "x2", "y2")),
          "구역 박스 좌표가 0~1")
    check(box["x1"] < box["x2"] and box["y1"] < box["y2"], "x1<x2, y1<y2")
    check("위험구역" in box["label"] and "AMR#1" in box["label"],
          f"라벨이 사람이 읽을 이름 ({box['label']})")
    check("score" not in box, "구역에는 점수를 붙이지 않는다 (탐지가 아니라 설명이다)")
    check("도달범위" in reach.box()["label"], "도달범위 라벨")

    shape = corridor.shape()
    check(len(shape["points"]) >= 3 and all(len(p) == 2 for p in shape["points"]),
          "확장 필드에는 다각형이 그대로 들어간다")

    # 한 번도 움직인 적 없는 AMR 은 방향을 모른다 — 통로를 그리지 않는다
    still = make_track(AMR, 3, (5.0, 2.0), (0.0, 0.0))
    still.heading = (0.0, 0.0)
    check(zones.corridor_zone(still, tun, H_inv, "") is None,
          "방향을 모르는 AMR 의 통로는 그리지 않는다")

    # 미보정이면 그릴 수 없다
    check(zones.build([person], [amr], pairs, tun, None) == [],
          "보정이 없으면 구역도 없다")


def test_overlay_channel() -> None:
    """화면과 종합 현황이 **같은 그림**을 보는가. 그리고 그 그림이 계약을 지키는가."""
    print("오버레이 인터페이스")
    import json

    from _sdk import parse_frame

    pub = FakePublisher()
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_cfg(tmp)
        cfg.publish_live = True
        cfg.live_min_interval = 0.0
        pipe = CameraPipeline(1, cfg, pub, setup_of())
        for i in range(20):
            t = i * 0.2
            x = 1.0 + 0.8 * t
            pipe.offer(Frame(camera_id=1, module_id="yolo-server", ts="",
                             boxes=[box_at(6.0, 5.0, label="Human"),
                                    box_at(x, 5.0, w=0.10, h=0.08,
                                           label="자율주행로봇")], received=t))
            pipe.tick(t)

        over = pipe.overlay()
        check(over["calibrated"] is True, "오버레이가 보정 상태를 알려 준다")
        check(len(over["tracks"]) == 2, "트랙 둘(사람·AMR)")
        check({z["kind"] for z in over["zones"]} == {"corridor", "reach"},
              "구역 둘이 그림에 들어 있다")
        check(len(over["zone_boxes"]) == len(over["zones"]),
              "구역마다 계약 박스가 하나씩")

        # 브로커로 나간 라이브가 화면과 같은 내용인가
        check(bool(pub.live_raw), "라이브가 발행되었다")
        topic, last = pub.live_raw[-1]
        check(topic == "aivision/live/1", f"라이브 토픽 모양 ({topic})")
        check(last["module_id"] == "collision" and last["camera_id"] == 1,
              "라이브 payload 의 발행자·카메라")
        labels = {b["label"] for b in last["boxes"]}
        check(any("위험구역" in x for x in labels), "라이브 박스에 구역이 실린다")
        check(any("사람" in x or "Human" in x for x in labels), "원본 트랙 박스도 함께")
        check("shapes" in last and last["shapes"], "확장 필드 shapes 가 함께 실린다")

        # 계약 2장 — 모든 박스가 0~1 이고 라벨이 사람이 읽을 이름이다
        for b in last["boxes"]:
            check(all(0.0 <= b[k] <= 1.0 for k in ("x1", "y1", "x2", "y2")),
                  f"라이브 박스 0~1 ({b['label']})")

        # 남이 받아 쓸 수 있어야 한다 — SDK 의 parse_frame 이 그대로 읽는가
        frame = parse_frame("aivision/live/1",
                            json.dumps(last, ensure_ascii=False).encode())
        check(frame is not None and len(frame.boxes) == len(last["boxes"]),
              "우리 라이브를 parse_frame 이 그대로 읽는다 (shapes 는 무시된다)")

        # 구역을 빼면 원본 박스만 나간다
        cfg.overlay_zones = False
        pipe.configure(cfg, setup_of())
        pipe.tick(4.2)
        plain = pub.live_raw[-1][1]
        check(not any("위험구역" in b["label"] for b in plain["boxes"]),
              "구역을 끄면 라이브에서 빠진다")
        check("shapes" not in plain, "구역을 끄면 확장 필드도 없다")
        check(pipe.overlay()["zones"], "그래도 모듈 화면용 그림에는 남아 있다")


def test_config_env_and_file() -> None:
    """env 는 '처음 값', 파일은 '사람이 고친 값' 이고 파일이 이긴다."""
    print("설정 우선순위")
    cfg = config_module.Config()
    cfg.amr_labels = ["env-amr"]
    cfg.person_labels = ["env-person"]
    cfg.tuning = settings_module.defaults()

    # 파일에 라벨 키가 없다 — 아직 화면에 한 번도 안 들어간 현장이다. env 로 돈다.
    untouched = settings_module.Settings()
    untouched.tuning = {"eps_contact": 0.9}
    config_module.apply_settings(cfg, untouched)
    check(cfg.amr_labels == ["env-amr"], "파일에 없는 항목은 env 그대로")
    check(cfg.tuning["eps_contact"] == 0.9, "화면에서 고친 임계값이 이긴다")

    # 파일에 라벨 키가 있으면 그 값이 이긴다 (빈 목록도 사람의 결정이다)
    saved = settings_module.Settings()
    saved.labels_set = True
    saved.amr_labels = ["화면-amr"]
    saved.person_labels = ["화면-person"]
    config_module.apply_settings(cfg, saved)
    check(cfg.amr_labels == ["화면-amr"] and cfg.person_labels == ["화면-person"],
          "화면에서 고친 라벨이 env 를 이긴다")
    check(cfg.hold_sec == cfg.tuning["hold_sec"],
          "Debouncer 가 보는 값과 임계값 표가 어긋나지 않는다")
    check(cfg.capabilities == ["COLLISION", "COLLISION_RISK"],
          "capabilities 는 연결된 항목에서 나온다")
    cfg.risk_item = ""
    cfg.collision_item = ""
    check(cfg.capabilities == [], "항목을 비우면 선언할 것이 없다")


def main() -> int:
    for fn in (test_homography, test_sweep_and_corridor, test_tracking, test_risk,
               test_collision, test_transitions, test_uncalibrated_and_disabled,
               test_garbage_input, test_platform_contract, test_live_roundtrip,
               test_zone_geometry, test_zone_build, test_overlay_channel,
               test_settings_store, test_settings_seeding, test_config_env_and_file):
        fn()
    print(f"\n검증 {CHECKS}개 통과 ({time.strftime('%H:%M:%S')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""카메라 한 대의 파이프라인 — 받은 박스에서 이벤트까지.

    구독 → (버퍼) → 트래킹 → 호모그래피 → 칼만 → 특징량 → 판정 → 전이 → 발행

**콜백에서 일하지 않는다**(계약 3장). `offer()` 는 MQTT 네트워크 스레드에서 불리고
받은 것을 버퍼에 넣기만 한다. 판정은 `tick()` 이고 그것은 우리 스레드에서 돈다. 이
경계를 흐리면 브로커 메시지가 밀리고, 밀린 것은 조용히 늦어진다.

**측정과 예측을 구분한다.** 발행자는 초당 몇 장을 내는데 우리는 그보다 촘촘히 틱을
돈다. 같은 프레임을 여러 번 측정으로 먹이면 칼만이 '이 물체는 멈춰 있다' 고 배워
속도가 0 으로 주저앉는다. 새로 도착한 프레임만 측정으로 쓰고, 나머지 틱은 예측만 한다.

**짝짓기는 도착 시각으로**(계약 3장). AMR 을 내는 발행자와 사람을 내는 발행자가 다르고,
그들의 `ts` 는 초 단위에 시계도 다르다. 같은 순간으로 묶는 기준은 `Frame.received` 다.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import geometry
import judge
import tracking
import zones as zones_module
from _sdk import Debouncer, Frame
from _sdk.publisher import DETECT_TOPIC, LIVE_TOPIC, now_iso
from settings import CameraSetup, Thresholds

log = logging.getLogger(__name__)

# 같은 물체를 두 발행자가 함께 낼 때 하나로 본다. 두 트랙이 되면 같은 AMR 이 두 대로
# 보이고, 사람 한 명에 위험 쌍이 두 개 생긴다.
DEDUPE_IOU = 0.7

# 수준을 모른 채 위험을 발행해야 하는 경우(트랙이 방금 사라진 틈)에 적는 값. 위험을
# 발행하면서 수준을 비워 두면 받는 쪽이 "level 이 없다" 를 따로 다뤄야 한다.
WARN_FALLBACK = judge.WARN


@dataclass
class Judgement:
    """화면과 로그에 남기는 한 줄. 이벤트 발행과는 별개다 —
    발행은 전이 순간만 나가지만, 사람은 '무슨 일이 있었나' 를 보고 싶어 한다."""

    ts: str
    camera_id: int
    kind: str                        # risk | collision
    state: str                       # active | inactive
    detail: str
    level: str = ""
    grade: str = ""
    evidence: str = ""               # 스냅샷 파일 이름


class CameraPipeline:
    """카메라 한 대. 트랙 상태도 호모그래피도 카메라마다 따로다."""

    def __init__(self, camera_id: int, cfg, publisher, setup: CameraSetup,
                 on_judgement=None, snapshot=None) -> None:
        self.camera_id = camera_id
        self.cfg = cfg
        self.pub = publisher
        self.on_judgement = on_judgement
        self.snapshot = snapshot

        self._lock = threading.Lock()
        self._inbox: dict[str, Frame] = {}
        self._consumed: dict[str, float] = {}

        self.tun: Thresholds = cfg.thresholds()
        self.setup = setup
        self.homography = setup.homography()
        # 판정은 이미지 → 월드 한 방향이면 되지만 **구역을 그리려면 반대 방향**이 필요하다.
        self.h_inv = geometry.invert(self.homography)

        self.amr = tracking.Tracker(tracking.AMR, max_age=self.tun.track_max_age)
        self.person = tracking.Tracker(tracking.PERSON, max_age=self.tun.track_max_age)
        self.watcher = judge.CollisionWatcher(camera_id)
        self.debouncer = Debouncer(hold_sec=self.tun.hold_sec, min_conf=self.tun.min_conf)

        self.received = 0
        self.published = 0
        self.last_input = 0.0            # 마지막으로 박스를 받은 시각
        self.publishers: set[str] = set()
        self.pairs: list[judge.Pair] = []
        self.zones: list[zones_module.Zone] = []
        # 화면이 읽어 갈 오버레이 한 벌. 판정 스레드가 만들어 **통째로 갈아 끼운다** —
        # API 스레드가 트래커를 직접 훑으면 도는 중인 자료구조를 읽게 된다.
        self._overlay: dict = _empty_overlay(camera_id)

        self._risk_level = ""            # 지금 이 순간의 수준
        self._risk_peak = 0              # 이번 에피소드에서 발행한 가장 높은 수준
        self._risk_reported = ""         # 마지막으로 발행한 수준 (해제에 실어 보낸다)
        self._last_conf: judge.Confirmation | None = None
        self._last_live = 0.0
        self._warned_uncalibrated = False

    # ── MQTT 스레드 ─────────────────────────────────────────────────

    def offer(self, frame: Frame) -> None:
        """받은 것을 넣기만 한다. 여기서 판정하면 브로커가 밀린다."""
        with self._lock:
            self._inbox[frame.module_id] = frame
            self.received += 1
            self.last_input = frame.received
            self.publishers.add(frame.module_id)

    # ── 설정 ────────────────────────────────────────────────────────

    def configure(self, cfg, setup: CameraSetup) -> None:
        """설정이 바뀌었다. 트랙을 버리지 않는다 — 임계값을 조금 고쳤다고 돌고 있는
        판정이 리셋되면, 화면에서 값을 만지는 동안 알람이 끊긴다."""
        self.cfg = cfg
        self.setup = setup
        self.homography = setup.homography()
        self.h_inv = geometry.invert(self.homography)
        self.tun = cfg.thresholds()
        self.amr.max_age = self.tun.track_max_age
        self.person.max_age = self.tun.track_max_age
        self.debouncer.hold_sec = self.tun.hold_sec
        self.debouncer.min_conf = self.tun.min_conf
        self._warned_uncalibrated = False

    @property
    def calibrated(self) -> bool:
        return self.homography is not None

    @property
    def enabled(self) -> bool:
        return self.setup.enabled

    # ── 판정 스레드 ─────────────────────────────────────────────────

    def tick(self, now: float) -> None:
        """한 틱. 새 박스가 있으면 측정까지, 없으면 예측과 만료만."""
        frames = self._take(now)

        if not self.enabled:
            self._release(now)
            return
        if not self.calibrated:
            if frames and not self._warned_uncalibrated:
                self._warned_uncalibrated = True
                log.warning("카메라 %d 는 아직 보정되지 않았습니다 — 박스는 받고 있지만 "
                            "거리를 잴 수 없어 판정하지 않습니다. 모듈 화면에서 바닥 4점을 "
                            "찍으세요.", self.camera_id)
            self._release(now)
            return

        self.amr.predict(now)
        self.person.predict(now)

        if frames:
            # 측정 시각은 틱 시각으로 통일한다. 트래커의 예측이 이미 여기까지 와 있고,
            # 프레임의 도착 시각과는 한 틱(기본 0.1초) 안쪽 차이다. 도착 시각은
            # **어떤 프레임끼리 같은 순간인가**(pair_window)를 가리는 데 쓴다.
            amr_dets, person_dets = self._detections(frames)
            self.amr.update(now, amr_dets)
            self.person.update(now, person_dets)

        self.amr.expire(now)
        self.person.expire(now)

        persons = self.person.confirmed()
        amrs = self.amr.confirmed()
        self.pairs = judge.assess(persons, amrs, self.tun)
        # 구역은 판정 다음에 만든다 — 색(level)이 방금 매긴 수준을 따라가야 한다.
        self.zones = zones_module.build(persons, amrs, self.pairs, self.tun, self.h_inv)

        confirmations = self.watcher.feed(now, self.pairs, self.tun)
        self.watcher.sweep(now, self.tun,
                           {t.track_id for t in persons} | {t.track_id for t in amrs})

        self._emit(now, confirmations)
        self._snapshot_overlay(persons, amrs)
        self._emit_live(now)

    def close(self, now: float) -> None:
        """할당이 빠지거나 모듈이 내려간다. 켜져 있던 것을 해제한다.

        이게 없으면 '발생' 만 남고 '해제' 가 오지 않아 화면에 영원히 켜져 있다.
        """
        for t in self.debouncer.close_camera(self.camera_id, now):
            self._publish(t.item, "inactive", t.confidence, t.boxes,
                          self._extra(t.item, "inactive"))
            self._note(t.item, "inactive", "담당에서 빠졌거나 모듈이 내려갑니다")
        if self.cfg.publish_live:
            self.pub.publish_live(self.cfg.module_id, self.camera_id, [])

    def status(self) -> dict:
        worst = max((p.level for p in self.pairs), key=lambda lv: judge.RANK[lv],
                    default="")
        return {
            "camera_id": self.camera_id,
            "enabled": self.enabled,
            "calibrated": self.calibrated,
            "points": len(self.setup.points),
            "received": self.received,
            "published": self.published,
            "publishers": sorted(self.publishers),
            "idle_sec": (round(time.monotonic() - self.last_input, 1)
                         if self.last_input else None),
            "tracks": {"amr": len(self.amr.confirmed()),
                       "person": len(self.person.confirmed())},
            "level": worst,
            "collisions": len(self.watcher.live()),
            "note": self.setup.note,
        }

    # ── 내부 ────────────────────────────────────────────────────────

    def _take(self, now: float) -> list[Frame]:
        """아직 안 먹은 프레임만 꺼낸다. 오래된 것은 짝짓기 창 밖이라 버린다."""
        with self._lock:
            frames = list(self._inbox.values())
        out = []
        for f in frames:
            if now - f.received > self.tun.pair_window:
                continue
            if f.received <= self._consumed.get(f.module_id, 0.0):
                continue
            self._consumed[f.module_id] = f.received
            out.append(f)
        return out

    def _detections(self, frames: list[Frame]) -> tuple[list, list]:
        """프레임의 박스를 AMR·사람으로 가르고 바닥 좌표를 붙인다.

        라벨은 설정에서 온다. 코드에 박으면 화면에서 이름을 고치는 순간 조용히 멈춘다.
        """
        amr_labels = set(self.cfg.amr_labels)
        person_labels = set(self.cfg.person_labels)
        amr_dets: list[tuple[dict, tuple[float, float]]] = []
        person_dets: list[tuple[dict, tuple[float, float]]] = []

        for f in frames:
            for box in f.boxes:
                label = str(box.get("label") or "")
                if label in amr_labels:
                    bucket = amr_dets
                elif label in person_labels:
                    bucket = person_dets
                else:
                    continue
                world = self._world(box)
                if world is None:
                    continue
                bucket.append((box, world))
        return (_dedupe(amr_dets), _dedupe(person_dets))

    def _world(self, box: dict) -> tuple[float, float] | None:
        try:
            u, v = geometry.foot_point(box)
        except (KeyError, TypeError, ValueError):
            return None                      # 남의 버그로 죽지 않는다 (계약 3장)
        return geometry.project(self.homography, u, v)

    def _emit(self, now: float, confirmations: list[judge.Confirmation]) -> None:
        risky = [p for p in self.pairs if p.level]
        level = max((p.level for p in risky), key=lambda lv: judge.RANK[lv], default="")
        self._risk_level = level

        found: dict[str, list[dict]] = {}
        if self.cfg.risk_item and risky:
            found[self.cfg.risk_item] = _boxes_of(risky)
        live = self.watcher.live()
        if self.cfg.collision_item and live:
            found[self.cfg.collision_item] = _boxes_of(
                [e.pair for e in live if e.pair is not None])

        first = confirmations[0] if confirmations else None
        if first is not None:
            self._last_conf = first

        consumed_first = False
        for t in self.debouncer.observe(self.camera_id, now, found):
            self._publish(t.item, t.state, t.confidence, t.boxes,
                          self._extra(t.item, t.state))
            self._note(t.item, t.state, self._detail(t.item, t.state))
            if t.item == self.cfg.collision_item and t.state == "active":
                consumed_first = True
            if t.item == self.cfg.risk_item:
                if t.state == "active":
                    self._risk_peak = judge.RANK[level]
                    self._risk_reported = level
                else:
                    self._risk_peak = 0
                    self._risk_reported = ""

        # 확정이 여러 건이면(다른 쌍) 각각이 따로 볼 사고다. 첫 건은 위 전이가 이미 냈다.
        rest = confirmations[1:] if consumed_first else confirmations
        for conf in rest:
            if conf.pair.confidence() < self.tun.min_conf:
                continue
            self._publish(self.cfg.collision_item, "active", conf.pair.confidence(),
                          conf.pair.boxes(), conf.payload_extra())
            self._note(self.cfg.collision_item, "active", self._detail(
                self.cfg.collision_item, "active"), conf=conf)

        # 주의 -> 임박으로 올라가는 것은 사람이 알아야 할 변화다. 한 에피소드 안에서
        # **올라갈 때만** 한 번 낸다 — 오르내릴 때마다 내면 그것이 곧 깜빡임이다.
        if (level and self.cfg.risk_item and judge.RANK[level] > self._risk_peak
                and self._risk_peak > 0):
            conf_value = max((p.confidence() for p in risky), default=0.0)
            if conf_value >= self.tun.min_conf:
                self._risk_peak = judge.RANK[level]
                self._risk_reported = level
                self._publish(self.cfg.risk_item, "active", conf_value,
                              _boxes_of(risky), {"level": level})
                self._note(self.cfg.risk_item, "active", "주의에서 임박으로 올라갔습니다")

        for conf in confirmations:
            self._keep_evidence(conf)

    def _snapshot_overlay(self, persons, amrs) -> None:
        """이번 틱의 그림을 한 벌로 굳힌다. 화면과 라이브 발행이 **같은 것**을 본다.

        둘을 따로 만들면 모듈 화면과 종합 현황이 다른 그림을 그리게 되고, 그때는 어느
        쪽이 맞는지 아무도 모른다.
        """
        by_amr, by_person = zones_module.levels_by_track(self.pairs)
        tracks = []
        boxes = []
        for tr in list(persons) + list(amrs):
            level = (by_person if tr.kind == tracking.PERSON else by_amr).get(
                tr.track_id, "")
            box = dict(tr.box)
            mark = {judge.WARN: " · 주의", judge.IMMINENT: " · 임박"}.get(level, "")
            box["label"] = f"{tr.label()}#{tr.track_id}{mark}"
            boxes.append(box)
            tracks.append({
                "track_id": tr.track_id, "kind": tr.kind, "level": level,
                "label": tr.label(), "box": box,
                "speed_ms": round(tr.speed, 2),
                "world": [round(tr.pos[0], 2), round(tr.pos[1], 2)],
            })

        zone_boxes = [b for b in (z.box() for z in self.zones) if b is not None]
        self._overlay = {
            "camera_id": self.camera_id,
            "ts": now_iso(),
            "calibrated": self.calibrated,
            "enabled": self.enabled,
            "level": self._risk_level,
            "collisions": len(self.watcher.live()),
            "tracks": tracks,
            "boxes": boxes,
            "zone_boxes": zone_boxes,
            "zones": [z.shape() for z in self.zones],
        }

    def overlay(self) -> dict:
        """화면이 읽어 가는 그림 한 벌(모듈 REST). 판정 스레드가 굳혀 둔 것을 그대로 준다.

        **라이브 발행(`publish_live`)과 무관하게 항상 있다.** 브로커로 내보낼지 말지는
        운영 결정이고, 자기 화면에서 보는 것은 그것과 별개여야 한다.
        """
        return self._overlay

    def _emit_live(self, now: float) -> None:
        """선택 — 우리가 만든 그림(위험구역·도달범위)을 브로커로도 낸다. 쌓이지 않는다.

        **사람·AMR 박스는 싣지 않는다.** 그것은 남(객체감지·카메라 메타데이터)이 이미
        낸 것이고, 코어는 발행자별로 나눠 들고 있다가 합쳐 그린다 — 되실으면 같은 물체
        위에 사각형이 두 번 그려진다. 계약 문서의 '자기가 만든 것만 내십시오' 다.

        예전에는 상위집합을 냈다. 그때 코어가 카메라 단위로 박스를 통째로 갈아 끼워서,
        두 발행자의 그림이 번갈아 깜빡였기 때문이다. 코어가 고쳐졌으므로(module_id 로
        레이어를 나눈다) 그 회피책은 이제 해롭다.

        다각형은 `shapes` 라는 **확장 필드**로 함께 싣는다 — 코어는 모르는 키를 무시하고,
        원근이 살아 있는 그림이 필요한 쪽만 읽으면 된다.

        모듈 자기 화면은 영향받지 않는다. 그쪽은 `overlay()` 로 사람·AMR 까지 한 벌을
        그대로 받는다 — 자기 화면에서 보는 것과 남에게 보내는 것은 다른 결정이다.
        """
        if not self.cfg.publish_live or not self.cfg.overlay_zones:
            # 구역을 안 그릴 거면 낼 것이 없다. 빈 배열을 계속 내보내도 화면에 남는
            # 것은 없고 브로커만 시끄러워진다.
            return
        if now - self._last_live < self.cfg.live_min_interval:
            return
        self._last_live = now

        payload = {"camera_id": self.camera_id, "module_id": self.cfg.module_id,
                   "ts": now_iso(), "boxes": list(self._overlay["zone_boxes"]),
                   "shapes": self._overlay["zones"]}
        self.pub.publish(LIVE_TOPIC.format(camera_id=self.camera_id), payload)
        self.published += 1

    def _publish(self, item: str, state: str, confidence: float, boxes: list[dict],
                 extra: dict) -> None:
        """detect 발행. SDK 의 `publish_detection` 을 쓰지 않는 이유는 payload 에
        `level`·`grade` 를 더해야 하기 때문이다. 토픽과 필수 키는 계약 그대로다."""
        if not item:
            return                            # 항목이 연결되지 않았다 — 로그만 남는다
        payload = {
            "camera_id": self.camera_id, "module_id": self.cfg.module_id,
            "item": item, "state": state, "ts": now_iso(),
            "confidence": round(float(confidence), 3), "boxes": boxes,
        }
        payload.update(extra)
        self.pub.publish(DETECT_TOPIC.format(camera_id=self.camera_id,
                                             module_id=self.cfg.module_id), payload)
        self.published += 1

    def _extra(self, item: str, state: str) -> dict:
        if item and item == self.cfg.risk_item:
            # 해제에는 **끝난 알람의 수준**을 싣는다. 해제 시점의 수준은 이미 빈 값이라
            # 그것을 쓰면 임박으로 올라갔던 알람이 '주의가 끝났다' 로 기록된다.
            if state == "inactive":
                return {"level": self._risk_reported or self._risk_level or WARN_FALLBACK}
            return {"level": self._risk_level or WARN_FALLBACK}
        if item and item == self.cfg.collision_item and self._last_conf is not None:
            return self._last_conf.payload_extra()
        return {}

    def _detail(self, item: str, state: str) -> str:
        if item == self.cfg.risk_item:
            worst = max((p for p in self.pairs if p.level),
                        key=lambda p: (judge.RANK[p.level], -p.dist), default=None)
            if state == "inactive" or worst is None:
                return "위험 조건이 사라졌습니다"
            return (f"사람#{worst.person.track_id} × AMR#{worst.amr.track_id} · "
                    f"거리 {worst.dist:.2f}m · 접근 {worst.closing:.2f}m/s"
                    + (f" · τ {worst.tau:.2f}s" if worst.tau is not None else ""))
        if item == self.cfg.collision_item:
            if state == "inactive" or self._last_conf is None:
                return "충돌 상태가 끝났습니다"
            c = self._last_conf
            return (f"사람#{c.pair.person.track_id} × AMR#{c.pair.amr.track_id} · "
                    f"{judge.GRADE_NAMES.get(c.grade, c.grade)} · "
                    f"상대속도 {c.v_rel:.2f}m/s · 근거 "
                    + ", ".join(i.name for i in c.impacts))
        return ""

    def _release(self, now: float) -> None:
        """판정을 못 하는 상태(미보정·꺼 둠)에서도 켜져 있던 것은 풀어야 한다.

        그림도 함께 비운다. 안 그러면 판정을 멈춘 카메라에 마지막 구역이 그대로 남아,
        보정을 지운 뒤에도 화면에는 위험 구역이 떠 있게 된다.
        """
        self.zones = []
        self._overlay = _empty_overlay(self.camera_id, calibrated=self.calibrated,
                                       enabled=self.enabled)
        for t in self.debouncer.observe(self.camera_id, now, {}):
            self._publish(t.item, t.state, t.confidence, t.boxes,
                          self._extra(t.item, t.state))
            self._note(t.item, t.state, self._detail(t.item, t.state))
        self._risk_level = ""
        self._risk_peak = 0
        self._risk_reported = ""

    def _note(self, item: str, state: str, detail: str,
              conf: judge.Confirmation | None = None) -> None:
        if self.on_judgement is None:
            return
        kind = "collision" if item == self.cfg.collision_item else "risk"
        conf = conf or (self._last_conf if kind == "collision" else None)
        self.on_judgement(Judgement(
            ts=now_iso(), camera_id=self.camera_id, kind=kind, state=state,
            detail=detail, level=self._risk_level if kind == "risk" else "",
            grade=(conf.grade if conf is not None and kind == "collision" else "")))

    def _keep_evidence(self, conf: judge.Confirmation) -> None:
        """확정 순간 스냅샷 한 장(기획 7장).

        영상을 열지 않는 이유: 박스만으로 판정이 끝났다. 사후에 사람이 '정말 그랬나' 를
        보려면 한 장이면 되고, 그것이 가장 가볍다.
        """
        if not self.cfg.evidence_snapshot or self.snapshot is None:
            return
        try:
            name = self.snapshot(self.camera_id)
        except Exception:                                       # noqa: BLE001
            log.exception("증거 스냅샷 저장 실패 (카메라 %d)", self.camera_id)
            return
        if name and self.on_judgement is not None:
            self.on_judgement(Judgement(
                ts=now_iso(), camera_id=self.camera_id, kind="collision",
                state="evidence", detail="증거 스냅샷", grade=conf.grade, evidence=name))


def _empty_overlay(camera_id: int, *, calibrated: bool = False,
                   enabled: bool = True) -> dict:
    """그릴 것이 없을 때의 한 벌. 모양은 늘 같아야 화면이 분기하지 않는다."""
    return {"camera_id": camera_id, "ts": now_iso(), "calibrated": calibrated,
            "enabled": enabled, "level": "", "collisions": 0,
            "tracks": [], "boxes": [], "zone_boxes": [], "zones": []}


def _boxes_of(pairs: list[judge.Pair]) -> list[dict]:
    """쌍들에 걸린 박스를 한 벌로 모은다. 같은 사람이 두 AMR 에 걸리면 한 번만 넣는다."""
    out: list[dict] = []
    seen: set[int] = set()
    for p in pairs:
        for tr in (p.person, p.amr):
            if id(tr) in seen:
                continue
            seen.add(id(tr))
            out.append(dict(tr.box))
    return out


def _dedupe(dets: list[tuple[dict, tuple[float, float]]]) -> list:
    """같은 물체를 두 발행자가 냈을 때 점수 높은 쪽만 남긴다."""
    ordered = sorted(dets, key=lambda d: -float(d[0].get("score") or 0.0))
    kept: list[tuple[dict, tuple[float, float]]] = []
    for det in ordered:
        if any(tracking.iou(det[0], k[0]) >= DEDUPE_IOU for k in kept):
            continue
        kept.append(det)
    return kept

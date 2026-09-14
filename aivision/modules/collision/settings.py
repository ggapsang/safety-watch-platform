"""이 모듈의 설정 — 화면에서 고치고 자기 볼륨(`/config/collision.json`)에 남긴다.

**왜 코어에 넣지 않는가.** 코어에 저장하는 길은 없고(계약 4장), 있어서도 안 된다.
호모그래피 4점이니 사람 최대 보행속도니 하는 것은 이 모듈만의 사정이다. 코어가 그
모양을 알게 되면 코어가 충돌 판정을 알게 된다.

**env 와의 관계.** env 는 '처음 값', 파일은 '사람이 고친 값' 이고 파일이 이긴다.
파일이 없어도 모듈은 env 만으로 돈다 — 화면에 한 번도 안 들어간 현장도 그대로 동작해야 한다.

여기 담기는 것 네 가지.
  1. 라벨 매핑   어떤 라벨이 AMR 이고 어떤 라벨이 사람인가 (계약 3장: 라벨은 바뀐다)
  2. 항목 매핑   위험·충돌을 플랫폼의 어느 탐지 항목 코드로 낼 것인가
  3. 임계값      ε, Δt, T, v_h,max …
  4. 카메라 보정 카메라마다의 호모그래피 4점 (정규화 이미지 좌표 -> 월드 미터)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geometry

log = logging.getLogger(__name__)

FILENAME = "collision.json"
VERSION = 1

# 화면에서 고칠 수 있는 값: 이름 -> (최소, 최대, 기본). 범위를 한 곳에 둔다 —
# 화면과 API 가 따로 검사하면 언젠가 서로 다른 값을 통과시킨다.
# env 이름은 대문자로 같다 (eps_contact -> EPS_CONTACT).
TUNING: dict[str, tuple[float, float, float]] = {
    # ── 판정 공통 ──
    "pair_window": (0.2, 5.0, 1.2),      # 이 시간 안에 도착한 발행자 판독끼리만 짝짓는다
    "track_max_age": (0.3, 10.0, 1.5),   # 이 시간 안 보이면 트랙을 버린다(가림 견디기)
    "hold_sec": (0.5, 30.0, 3.0),        # 이 시간 동안 조건이 사라져야 '해제'
    "min_conf": (0.0, 0.99, 0.4),        # 이벤트로 올릴 최소 신뢰도(발행자 점수)
    # ── 위험 알람 (기획 6장) ──
    "r_amr": (0.1, 3.0, 0.5),            # AMR 반경 (m)
    "r_h": (0.1, 1.5, 0.35),             # 사람 반경 (m)
    "sigma": (0.0, 1.5, 0.2),            # 계측 오차 여유 σ (m)
    "v_h_max": (0.3, 3.0, 1.4),          # 사람 최대 보행속도 v_h,max (m/s)
    # 주의 통로 길이를 정하는 전망 시간 T (s). **임박보다 넉넉해야 한다** — 주의 구역이
    # 임박 구역을 품지 못하면 주의를 건너뛰고 임박부터 뜬다. 느린 AMR(0.3m/s 이하)에서는
    # 사람의 최악 도달 반경이 통로보다 커서 여전히 임박이 먼저 뜰 수 있다(기획 10장).
    "t_warn": (0.5, 15.0, 5.0),
    "t_imminent": (0.2, 5.0, 1.0),       # 임박 판정 전망 시간 (s)
    "corridor_half_w": (0.2, 3.0, 0.8),  # 통로 반폭 (m)
    "corridor_min_len": (0.5, 10.0, 2.0),  # 멈춰 있어도 이만큼은 통로로 본다 (m)
    # ── 충돌 판독 (기획 7장) ──
    "eps_contact": (0.05, 3.0, 0.45),    # 접촉으로 보는 바닥 투영 거리 ε (m)
    "dt_window": (0.2, 5.0, 1.5),        # 접촉과 충격이 동시로 인정되는 창 Δt (s)
    "amr_stop_dv": (0.05, 3.0, 0.35),    # AMR 급정지로 보는 속력 하락 (m/s)
    "person_dv": (0.05, 3.0, 0.6),       # 사람 속도 스파이크 (m/s)
    "aspect_flip": (1.1, 4.0, 1.6),      # 전도로 보는 종횡비(w/h) 증가 배수
    "graze_v": (0.05, 3.0, 0.4),         # 이보다 느린 상대속도는 '접촉·스침'
    "severe_v": (0.2, 5.0, 1.2),         # 이보다 빠른 상대속도는 '심각'
}


@dataclass
class Thresholds:
    """판정이 읽는 값들. `dict` 를 그대로 넘기지 않는 이유는 오타 때문이다 —
    `tun["eps_contct"]` 는 런타임에야 터지고, 그것도 충돌이 일어난 순간에 터진다."""

    pair_window: float = TUNING["pair_window"][2]
    track_max_age: float = TUNING["track_max_age"][2]
    hold_sec: float = TUNING["hold_sec"][2]
    min_conf: float = TUNING["min_conf"][2]
    r_amr: float = TUNING["r_amr"][2]
    r_h: float = TUNING["r_h"][2]
    sigma: float = TUNING["sigma"][2]
    v_h_max: float = TUNING["v_h_max"][2]
    t_warn: float = TUNING["t_warn"][2]
    t_imminent: float = TUNING["t_imminent"][2]
    corridor_half_w: float = TUNING["corridor_half_w"][2]
    corridor_min_len: float = TUNING["corridor_min_len"][2]
    eps_contact: float = TUNING["eps_contact"][2]
    dt_window: float = TUNING["dt_window"][2]
    amr_stop_dv: float = TUNING["amr_stop_dv"][2]
    person_dv: float = TUNING["person_dv"][2]
    aspect_flip: float = TUNING["aspect_flip"][2]
    graze_v: float = TUNING["graze_v"][2]
    severe_v: float = TUNING["severe_v"][2]

    @classmethod
    def from_map(cls, values: dict[str, float]) -> "Thresholds":
        return cls(**{k: float(v) for k, v in values.items() if k in TUNING})


def defaults() -> dict[str, float]:
    return {name: spec[2] for name, spec in TUNING.items()}


def clamp(name: str, value: Any) -> float | None:
    """범위 안으로 자른다. 거절하지 않는 이유: 슬라이더를 끝까지 민 것을 오류로
    돌려주면 화면이 멈춘 것처럼 보인다."""
    spec = TUNING.get(name)
    if spec is None:
        return None
    lo, hi, _ = spec
    try:
        return min(hi, max(lo, float(value)))
    except (TypeError, ValueError):
        log.warning("임계값 %s 를 숫자로 읽지 못했습니다: %r", name, value)
        return None


@dataclass
class CalibPoint:
    """스냅샷 위의 한 점과 그 점의 실제 바닥 좌표.

    `u,v` 는 **정규화 이미지 좌표**(0~1)다. 픽셀로 두면 스트림 프로파일이 바뀔 때
    보정이 통째로 틀어진다(계약 2장).
    """

    u: float
    v: float
    x: float                 # 월드 X (m)
    y: float                 # 월드 Y (m)

    def to_dict(self) -> dict:
        return {"u": self.u, "v": self.v, "x": self.x, "y": self.y}


@dataclass
class CameraSetup:
    """카메라 한 대의 보정과 사용 여부."""

    points: list[CalibPoint] = field(default_factory=list)
    enabled: bool = True
    note: str = ""

    def to_dict(self) -> dict:
        return {"points": [p.to_dict() for p in self.points],
                "enabled": self.enabled, "note": self.note}

    def pairs(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        return [((p.u, p.v), (p.x, p.y)) for p in self.points]

    def homography(self):
        """4점에서 H 를 계산한다. **행렬을 파일에 저장하지 않는다** — 점이 진실이고
        행렬은 그 유도물이다. 둘 다 저장하면 언젠가 서로 어긋난다."""
        return geometry.homography_from_points(self.pairs())

    @property
    def calibrated(self) -> bool:
        return len(self.points) >= 4 and self.homography() is not None


@dataclass
class Settings:
    # 라벨은 고정된 코드가 아니다. 화면에서 사람이 바꾼다(계약 3장).
    amr_labels: list[str] = field(default_factory=list)
    person_labels: list[str] = field(default_factory=list)
    # 플랫폼 탐지 항목 코드. 비우면 그 판정은 **이벤트를 만들지 않는다** —
    # 항목은 사람이 플랫폼에 먼저 만들어야 한다(계약 7장의 부채).
    risk_item: str = ""
    collision_item: str = ""
    tuning: dict[str, float] = field(default_factory=dict)
    cameras: dict[int, CameraSetup] = field(default_factory=dict)

    # 그림을 브로커로도 낼지(종합 현황에 보이게 할지), 구역을 함께 실을지.
    # 모듈 자기 화면은 이 값과 무관하게 늘 그린다 — 자기 화면에서 보는 것과 남에게
    # 보내는 것은 다른 결정이다.
    publish_live: bool = False
    overlay_zones: bool = True

    # 파일에 그 항목이 **있었는지**. 빈 값과 '아직 정한 적 없음' 은 다른 뜻이다 —
    # 빈 항목 코드는 '이벤트를 만들지 마라' 는 사람의 결정이고, 없는 키는 'env 를 따르라' 다.
    # 이 둘을 뭉개면 화면에서 라벨만 저장해도 항목 연결이 조용히 풀린다.
    labels_set: bool = False
    items_set: bool = False
    overlay_set: bool = False

    def setup(self, camera_id: int) -> CameraSetup:
        return self.cameras.get(int(camera_id)) or CameraSetup()

    def adopt(self, *, amr_labels: list[str], person_labels: list[str],
              risk_item: str, collision_item: str,
              publish_live: bool = False, overlay_zones: bool = True) -> None:
        """파일에 아직 없는 것을 지금 돌고 있는 값(env 기본)으로 채운다.

        그래야 화면에서 무엇 하나를 저장할 때 파일이 **지금 도는 설정 그대로** 쓰인다.
        이것이 없으면 라벨만 저장했는데 파일에는 빈 항목 코드가 적히고, 다음 기동에서
        그 빈 값이 '사람의 결정' 으로 읽힌다.
        """
        if not self.labels_set:
            self.amr_labels = list(amr_labels)
            self.person_labels = list(person_labels)
            self.labels_set = True
        if not self.items_set:
            self.risk_item = risk_item
            self.collision_item = collision_item
            self.items_set = True
        if not self.overlay_set:
            self.publish_live = publish_live
            self.overlay_zones = overlay_zones
            self.overlay_set = True

    def merged_tuning(self, base: dict[str, float]) -> dict[str, float]:
        """env 기본값 위에 사람이 고친 값을 덮는다."""
        out = dict(base)
        for name, value in self.tuning.items():
            cut = clamp(name, value)
            if cut is not None:
                out[name] = cut
        return out

    def set_tuning(self, values: dict[str, Any]) -> dict[str, float]:
        for name, value in values.items():
            cut = clamp(name, value)
            if cut is not None:
                self.tuning[name] = cut
        return dict(self.tuning)

    def to_dict(self) -> dict:
        return {
            "version": VERSION,
            "labels": {"amr": self.amr_labels, "person": self.person_labels},
            "items": {"risk": self.risk_item, "collision": self.collision_item},
            "overlay": {"publish_live": self.publish_live, "zones": self.overlay_zones},
            "tuning": self.tuning,
            "cameras": {str(cid): s.to_dict() for cid, s in sorted(self.cameras.items())},
        }


def _parse_points(raw: Any) -> list[CalibPoint]:
    out: list[CalibPoint] = []
    for p in raw or []:
        if not isinstance(p, dict):
            continue
        try:
            out.append(CalibPoint(u=float(p["u"]), v=float(p["v"]),
                                  x=float(p["x"]), y=float(p["y"])))
        except (KeyError, TypeError, ValueError):
            log.warning("보정 점을 읽지 못해 건너뜁니다: %r", p)
    return out


def _parse(data: dict) -> Settings:
    s = Settings()
    labels = data.get("labels") or {}
    s.labels_set = "labels" in data
    s.amr_labels = [str(x) for x in (labels.get("amr") or []) if str(x).strip()]
    s.person_labels = [str(x) for x in (labels.get("person") or []) if str(x).strip()]
    items = data.get("items") or {}
    s.items_set = "items" in data
    s.risk_item = str(items.get("risk") or "")
    s.collision_item = str(items.get("collision") or "")
    overlay = data.get("overlay") or {}
    s.overlay_set = "overlay" in data
    s.publish_live = bool(overlay.get("publish_live", False))
    s.overlay_zones = bool(overlay.get("zones", True))
    for name, value in (data.get("tuning") or {}).items():
        cut = clamp(str(name), value)
        if cut is not None:
            s.tuning[str(name)] = cut
    for cid, raw in (data.get("cameras") or {}).items():
        if not isinstance(raw, dict):
            continue
        try:
            camera_id = int(cid)
        except (TypeError, ValueError):
            continue
        s.cameras[camera_id] = CameraSetup(
            points=_parse_points(raw.get("points")),
            enabled=bool(raw.get("enabled", True)),
            note=str(raw.get("note") or ""))
    return s


def load(config_dir: Path) -> Settings:
    """설정을 읽는다. 없거나 깨졌으면 빈 설정 — 기동을 막지 않는다.

    설정 파일 하나 때문에 모듈이 안 뜨면 현장에서 손쓸 방법이 없다. 보정이 없으면
    판정을 안 할 뿐, 등록과 heartbeat 는 그대로 돌아야 화면에서 고칠 수 있다.
    """
    path = Path(config_dir) / FILENAME
    if not path.is_file():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("설정 파일을 읽지 못했습니다 (%s): %s — env 값으로 진행합니다", path, exc)
        return Settings()
    if not isinstance(data, dict):
        log.error("설정 파일의 최상위가 객체가 아닙니다 (%s) — 무시합니다", path)
        return Settings()
    return _parse(data)


def save(config_dir: Path, s: Settings) -> None:
    """원자적으로 쓴다. 쓰다 죽어도 반쯤 쓰인 파일이 남지 않는다."""
    root = Path(config_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / FILENAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s.to_dict(), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    tmp.replace(path)

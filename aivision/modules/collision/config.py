"""이 모듈의 설정 객체.

공통 항목(플랫폼 주소·브로커·폴링 주기)은 SDK 의 `BaseConfig` 가 읽는다. 여기서 더하는
것은 **입력이 영상이 아니라 남의 박스**라는 데서 오는 것들이다 — 누구의 발행을 먹을지
(`SRC_MODULES`), 어떤 라벨이 AMR 이고 사람인지, 그리고 판정 임계값이다.

라벨과 발행자를 코드에 박지 않는 이유는 계약 3장에 적혀 있다. 라벨은 화면에서 사람이
바꿀 수 있고, 발행자는 현장마다 다르다. `"Human"` 을 코드에 박아 두면 누가 "사람" 으로
고치는 순간 이 모듈은 **조용히** 멈춘다 — 아무 에러도 없이 알람만 안 온다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from _sdk import BaseConfig, env, flag, num

import settings as settings_module

log = logging.getLogger(__name__)


def _list(name: str, *fallbacks: str, default: tuple[str, ...] = ()) -> list[str]:
    """쉼표로 나눈 목록을 읽는다. 라벨에 공백이 들어갈 수 있어 좌우만 다듬는다."""
    raw = env(name)
    for alt in fallbacks:
        if not raw:
            raw = env(alt)
    if not raw:
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass
class Config(BaseConfig):
    module_id: str = "collision"
    module_name: str = "충돌 위험"

    # ── 입력 (계약 3장) ──
    # 구독할 발행자. 비우면 **모두** 받는다 — 자기 출력만은 언제나 뺀다(되먹임 방지).
    src_modules: list[str] = field(default_factory=list)
    amr_labels: list[str] = field(default_factory=list)
    person_labels: list[str] = field(default_factory=list)

    # ── 출력 ──
    # 플랫폼 탐지 항목 코드. 비우면 그 판정은 로그만 남기고 이벤트를 만들지 않는다.
    risk_item: str = "COLLISION_RISK"
    collision_item: str = "COLLISION"
    # 라이브 오버레이(기획 2.3 선택). 기본은 끈다 — 화면용일 뿐인데 켜 두면 초당 여러 건이
    # 브로커를 지나고, 다른 규칙 플러그인이 그것을 또 먹을 수 있다.
    #
    # 켤 때 알아야 할 것: 코어 대시보드는 **카메라별로 마지막 라이브 메시지의 박스를
    # 통째로 교체**한다(web/src/lib/hooks.ts). 같은 카메라에 객체감지 모듈도 라이브를
    # 내고 있으면 두 발행자의 그림이 번갈아 보인다. 우리 그림이 상위집합(원본 박스 +
    # 구역)이므로, 구역까지 보려면 다른 발행자의 라이브를 끄는 편이 낫다.
    publish_live: bool = False
    # 위험 구역을 라이브에 함께 실을지. 모듈 자기 화면은 이 값과 무관하게 늘 그린다.
    overlay_zones: bool = True

    # ── 판정 ──
    # 판정 루프 주기. 발행자가 초당 3장을 내도 트랙은 그보다 촘촘히 굴러야 한다 —
    # 가림 구간에서 예측으로 버티고, 임박 판정이 한 박자 늦지 않으려면 그렇다.
    tick_hz: float = 10.0
    tuning: dict[str, float] = field(default_factory=settings_module.defaults)

    # ── 증거 (기획 7장) ──
    # 확정 순간 스냅샷 한 장. 영상은 열지 않는다 — 박스만으로 판정이 끝나기 때문이다.
    evidence_snapshot: bool = True
    evidence_keep: int = 200             # 이보다 오래된 증거 파일은 지운다

    # ── 화면 ──
    serve_port: int = 8000
    public_url: str = "http://localhost:12010"
    config_dir: str = "/config"

    @property
    def capabilities(self) -> list[str]:
        """'나는 이 항목들을 낸다' 는 선언. 선언일 뿐이고, 항목 자체는 사람이 플랫폼에
        먼저 만들어야 한다(계약 7장)."""
        return sorted({c for c in (self.risk_item, self.collision_item) if c})

    def thresholds(self) -> settings_module.Thresholds:
        return settings_module.Thresholds.from_map(self.tuning)

    @property
    def evidence_dir(self) -> Path:
        return Path(self.config_dir) / "evidence"


def load() -> Config:
    cfg = Config()
    cfg.load_base()

    cfg.src_modules = _list("SRC_MODULES")
    cfg.amr_labels = _list("AMR_LABEL", "AMR_LABELS")
    cfg.person_labels = _list("PERSON_LABEL", "PERSON_LABELS")
    cfg.risk_item = env("RISK_ITEM", cfg.risk_item)
    cfg.collision_item = env("COLLISION_ITEM", cfg.collision_item)
    cfg.publish_live = flag("PUBLISH_LIVE", False)
    cfg.overlay_zones = flag("OVERLAY_ZONES", True)
    cfg.tick_hz = max(1.0, num("TICK_HZ", cfg.tick_hz))
    cfg.evidence_snapshot = flag("EVIDENCE_SNAPSHOT", True)
    cfg.evidence_keep = int(num("EVIDENCE_KEEP", cfg.evidence_keep))

    cfg.serve_port = int(num("SERVE_PORT", cfg.serve_port))
    cfg.public_url = env("PUBLIC_URL", cfg.public_url).rstrip("/")
    cfg.config_dir = env("CONFIG_DIR", cfg.config_dir)

    # 임계값의 env 이름은 대문자로 같다 (eps_contact -> EPS_CONTACT).
    # hold_sec·min_conf 는 BaseConfig 가 이미 읽었으므로 그 값을 기본으로 삼는다.
    base = settings_module.defaults()
    base["hold_sec"] = cfg.hold_sec
    base["min_conf"] = cfg.min_conf
    for name in settings_module.TUNING:
        cut = settings_module.clamp(name, num(name.upper(), base[name]))
        if cut is not None:
            base[name] = cut
    cfg.tuning = base

    apply_settings(cfg, settings_module.load(Path(cfg.config_dir)))

    if not cfg.amr_labels or not cfg.person_labels:
        log.warning("AMR·사람 라벨이 비어 있습니다 — 받은 박스를 어느 쪽으로도 분류하지 "
                    "못해 판정이 돌지 않습니다. 모듈 화면(%s)에서 라벨을 지정하세요.",
                    cfg.public_url)
    if not cfg.capabilities:
        log.warning("탐지 항목 코드가 비어 있습니다 — 판정해도 이벤트를 만들지 않습니다. "
                    "플랫폼에 항목을 만들고 모듈 화면(%s)에서 연결하세요.", cfg.public_url)
    return cfg


def apply_settings(cfg: Config, s: settings_module.Settings) -> None:
    """설정 파일의 값을 설정 객체에 덮어쓴다.

    기동할 때와 화면에서 값을 고쳤을 때 **같은 함수**를 쓴다. 두 경로가 갈리면
    '화면에서 바꾼 것과 재시작 후가 다른' 일이 생긴다.
    """
    # 빈 값도 사람의 뜻이다 — 항목을 비우는 것은 '이벤트를 만들지 마라' 이고, 라벨을
    # 비우는 것은 '그 종류는 보지 마라' 다. 그래서 값이 아니라 **키가 있었는지**로 가른다.
    if s.labels_set:
        cfg.amr_labels = list(s.amr_labels)
        cfg.person_labels = list(s.person_labels)
    if s.items_set:
        cfg.risk_item = s.risk_item
        cfg.collision_item = s.collision_item
    if s.overlay_set:
        cfg.publish_live = s.publish_live
        cfg.overlay_zones = s.overlay_zones

    cfg.tuning = s.merged_tuning(cfg.tuning)
    # Debouncer 는 이 두 값을 직접 본다. 임계값 표와 어긋나지 않게 같이 옮긴다.
    cfg.hold_sec = cfg.tuning["hold_sec"]
    cfg.min_conf = cfg.tuning["min_conf"]

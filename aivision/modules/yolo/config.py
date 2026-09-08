"""사이드카 설정 — 전부 환경변수로 받는다.

모델이 바뀔 때 고쳐야 하는 것은 이 파일이 읽는 값들뿐이다. 특히 CLASS_MAP —
모델의 클래스를 플랫폼의 탐지 항목 코드로 옮기는 표다. 코드는 건드리지 않는다.

플랫폼 주소를 환경변수로 받는 이유: 같은 PC 의 사이드카일 수도, 다른 PC 의 원격
모듈일 수도 있다. 모듈은 자기가 어디서 도는지에 대한 가정을 갖지 않는다.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _num(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("%s='%s' 를 숫자로 읽을 수 없습니다 — 기본값 %s 사용", name, raw, default)
        return default


def _flag(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


VALID_LAYOUT = ("auto", "v5", "v8", "nms")


def _layout() -> str:
    raw = _env("LAYOUT", "auto").lower() or "auto"
    if raw not in VALID_LAYOUT:
        log.warning("LAYOUT='%s' 는 알 수 없는 값입니다 (%s) — auto 로 진행합니다",
                    raw, " | ".join(VALID_LAYOUT))
        return "auto"
    return raw


def _class_map() -> dict[str, str]:
    """모델 클래스 -> 탐지 항목 코드.

    키는 클래스 이름("no_helmet") 이거나 인덱스 문자열("0") 둘 다 받는다. 모델이
    이름을 갖고 있지 않은 경우가 흔해서다. 여기 없는 클래스는 무시한다 —
    모델이 80 클래스를 알더라도 이 현장이 관심 있는 것만 이벤트가 된다.
    """
    raw = _env("CLASS_MAP")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("CLASS_MAP 이 올바른 JSON 이 아닙니다 (%s) — 빈 표로 진행합니다", exc)
        return {}
    if not isinstance(data, dict):
        log.error("CLASS_MAP 은 {\"클래스\": \"항목코드\"} 형태여야 합니다")
        return {}
    return {str(k): str(v) for k, v in data.items()}


@dataclass
class Config:
    # ── 플랫폼 ──
    platform_url: str = "http://server:8000"
    module_id: str = "yolo-server"
    module_name: str = "서버 YOLO"
    mqtt_host: str = "mqtt"
    mqtt_port: int = 1883

    # ── 모델 ──
    model_path: Path | None = None
    device: str = "cuda"                 # cuda 불가 시 백엔드가 CPU 로 폴백한다
    imgsz: int = 640
    conf_thres: float = 0.25
    iou_thres: float = 0.45
    class_map: dict[str, str] = field(default_factory=dict)
    # auto | v5 | v8 | nms. 자동 판별이 틀리는 모델을 만났을 때 못 박는 자리다.
    layout: str = "auto"

    # ── 판정 ──
    sample_fps: float = 3.0              # 초당 몇 장만 본다. 전 프레임 추론은 낭비다
    hold_sec: float = 3.0                # 이 시간 동안 안 보이면 해제로 본다
    min_conf: float = 0.4                # 이벤트로 올릴 최소 신뢰도

    # ── 배관 ──
    work_poll_sec: float = 20.0
    heartbeat_sec: float = 30.0
    publish_live: bool = True            # 라이브 오버레이 박스 발행
    live_min_interval: float = 0.3       # 라이브 발행 최소 간격(초). 브라우저 보호
    dry_run: bool = False
    log_level: str = "INFO"

    @property
    def capabilities(self) -> list[str]:
        """플랫폼에 '나는 이 항목들을 판정한다' 고 알리는 목록. CLASS_MAP 에서 나온다."""
        return sorted(set(self.class_map.values()))


def load() -> Config:
    model = _env("MODEL_PATH")
    cfg = Config(
        platform_url=_env("PLATFORM_URL", "http://server:8000").rstrip("/"),
        module_id=_env("MODULE_ID", "yolo-server"),
        module_name=_env("MODULE_NAME", "서버 YOLO"),
        mqtt_host=_env("MQTT_HOST", "mqtt"),
        mqtt_port=int(_num("MQTT_PORT", 1883)),
        model_path=Path(model) if model else None,
        device=_env("DEVICE", "cuda"),
        imgsz=int(_num("IMGSZ", 640)),
        conf_thres=_num("CONF_THRES", 0.25),
        iou_thres=_num("IOU_THRES", 0.45),
        class_map=_class_map(),
        layout=_layout(),
        sample_fps=_num("SAMPLE_FPS", 3.0),
        hold_sec=_num("HOLD_SEC", 3.0),
        min_conf=_num("MIN_CONF", 0.4),
        work_poll_sec=_num("WORK_POLL_SEC", 20.0),
        heartbeat_sec=_num("HEARTBEAT_SEC", 30.0),
        publish_live=_flag("PUBLISH_LIVE", True),
        live_min_interval=_num("LIVE_MIN_INTERVAL", 0.3),
        dry_run=_flag("DRY_RUN", False),
        log_level=_env("LOG_LEVEL", "INFO"),
    )
    if not cfg.dry_run and (cfg.model_path is None or not cfg.model_path.is_file()):
        # 죽이지 않고 dry-run 으로 내려앉는다. 모델이 아직 없는 환경에서 컨테이너가
        # 재시작을 반복하는 것보다, 배관이 도는 것을 보여 주는 편이 낫다.
        log.warning("모델 파일(%s)이 없습니다 — dry-run 으로 전환합니다", cfg.model_path)
        cfg.dry_run = True
    if not cfg.class_map:
        log.warning("CLASS_MAP 이 비어 있습니다 — 판정 결과를 항목 코드로 옮길 수 없습니다. "
                    "플랫폼에서 탐지 항목을 먼저 만들고 CLASS_MAP 을 채우세요.")
    return cfg

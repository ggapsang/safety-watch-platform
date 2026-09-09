"""환경변수 읽기 + 모든 모듈이 공유하는 설정.

모듈마다 다른 설정(모델 경로, 클래스 매핑 등)은 각자 자기 config 에서 이 헬퍼로 읽는다.
SDK 는 '플랫폼과 말을 맞추는 데 필요한 것'만 안다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def num(name: str, default: float) -> float:
    raw = env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("%s='%s' 를 숫자로 읽을 수 없습니다 — 기본값 %s 사용", name, raw, default)
        return default


def flag(name: str, default: bool = False) -> bool:
    raw = env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass
class BaseConfig:
    # ── 플랫폼 ──
    platform_url: str = "http://base-app:8000"
    module_id: str = "module"
    module_name: str = "모듈"
    mqtt_host: str = "base-broker"
    mqtt_port: int = 1883

    # ── 판정 ──
    hold_sec: float = 3.0                # 이 시간 동안 안 보이면 해제로 본다
    min_conf: float = 0.4                # 이벤트로 올릴 최소 신뢰도

    # ── 배관 ──
    work_poll_sec: float = 20.0
    heartbeat_sec: float = 30.0
    publish_live: bool = True
    live_min_interval: float = 0.3       # 라이브 발행 최소 간격(초). 브라우저 보호
    log_level: str = "INFO"

    # 모델 클래스·메타데이터 클래스 -> 플랫폼 탐지 항목 코드
    class_map: dict[str, str] = field(default_factory=dict)

    @property
    def capabilities(self) -> list[str]:
        """플랫폼에 '나는 이 항목들을 판정한다' 고 알리는 목록."""
        return sorted(set(self.class_map.values()))

    def load_base(self) -> None:
        """환경변수에서 공통 항목을 채운다. 모듈 config 가 자기 것을 더 읽는다."""
        self.platform_url = env("PLATFORM_URL", self.platform_url).rstrip("/")
        self.module_id = env("MODULE_ID", self.module_id)
        self.module_name = env("MODULE_NAME", self.module_name)
        self.mqtt_host = env("MQTT_HOST", self.mqtt_host)
        self.mqtt_port = int(num("MQTT_PORT", self.mqtt_port))
        self.hold_sec = num("HOLD_SEC", self.hold_sec)
        self.min_conf = num("MIN_CONF", self.min_conf)
        self.work_poll_sec = num("WORK_POLL_SEC", self.work_poll_sec)
        self.heartbeat_sec = num("HEARTBEAT_SEC", self.heartbeat_sec)
        self.publish_live = flag("PUBLISH_LIVE", self.publish_live)
        self.live_min_interval = num("LIVE_MIN_INTERVAL", self.live_min_interval)
        self.log_level = env("LOG_LEVEL", self.log_level)


def class_map_from_env(name: str = "CLASS_MAP") -> dict[str, str]:
    """탐지 클래스 -> 플랫폼 탐지 항목 코드.

    여기 없는 클래스는 무시한다. 카메라가 Human·Face·Vehicle 을 다 알더라도 이 현장이
    관심 있는 것만 이벤트가 된다.
    """
    import json

    raw = env(name)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("%s 가 올바른 JSON 이 아닙니다 (%s) — 빈 표로 진행합니다", name, exc)
        return {}
    if not isinstance(data, dict):
        log.error('%s 는 {"클래스": "항목코드"} 형태여야 합니다', name)
        return {}
    return {str(k): str(v) for k, v in data.items()}

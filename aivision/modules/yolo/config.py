"""이 모듈의 설정.

공통 항목(플랫폼 주소·브로커·발행 주기·판정 창)은 SDK 가 읽는다. 여기서는 모델과 관련된
것만 더한다. 모델이 바뀔 때 고쳐야 하는 것은 CLASS_MAP 하나뿐이다 — 모델의 클래스를
플랫폼의 탐지 항목 코드로 옮기는 표다. 코드는 건드리지 않는다.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, "/app")

from _sdk import BaseConfig, class_map_from_env, env, flag, num  # noqa: E402

log = logging.getLogger(__name__)

VALID_LAYOUT = ("auto", "v5", "v8", "nms")


def _layout() -> str:
    raw = env("LAYOUT", "auto").lower() or "auto"
    if raw not in VALID_LAYOUT:
        log.warning("LAYOUT='%s' 는 알 수 없는 값입니다 (%s) — auto 로 진행합니다",
                    raw, " | ".join(VALID_LAYOUT))
        return "auto"
    return raw


@dataclass
class Config(BaseConfig):
    module_id: str = "yolo-server"
    module_name: str = "서버 YOLO"

    # ── 모델 ──
    model_path: Path | None = None
    device: str = "cuda"                 # cuda 불가 시 백엔드가 CPU 로 폴백한다
    imgsz: int = 640
    conf_thres: float = 0.25
    iou_thres: float = 0.45
    # auto | v5 | v8 | nms. 자동 판별이 틀리는 모델을 만났을 때 못 박는 자리다.
    layout: str = "auto"

    # ── 판정 ──
    sample_fps: float = 3.0              # 초당 몇 장만 본다. 전 프레임 추론은 낭비다
    dry_run: bool = False


def load() -> Config:
    cfg = Config()
    cfg.load_base()
    cfg.class_map = class_map_from_env("CLASS_MAP")

    model = env("MODEL_PATH")
    cfg.model_path = Path(model) if model else None
    cfg.device = env("DEVICE", "cuda")
    cfg.imgsz = int(num("IMGSZ", 640))
    cfg.conf_thres = num("CONF_THRES", 0.25)
    cfg.iou_thres = num("IOU_THRES", 0.45)
    cfg.layout = _layout()
    cfg.sample_fps = num("SAMPLE_FPS", 3.0)
    cfg.dry_run = flag("DRY_RUN", False)

    if not cfg.dry_run and (cfg.model_path is None or not cfg.model_path.is_file()):
        # 죽이지 않고 dry-run 으로 내려앉는다. 모델이 아직 없는 환경에서 컨테이너가
        # 재시작을 반복하는 것보다, 배관이 도는 것을 보여 주는 편이 낫다.
        log.warning("모델 파일(%s)이 없습니다 — dry-run 으로 전환합니다", cfg.model_path)
        cfg.dry_run = True
    if not cfg.class_map:
        log.warning("CLASS_MAP 이 비어 있습니다 — 판정 결과를 항목 코드로 옮길 수 없습니다. "
                    "플랫폼에서 탐지 항목을 먼저 만들고 CLASS_MAP 을 채우세요.")
    return cfg

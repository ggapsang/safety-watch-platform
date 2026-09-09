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
    module_name: str = "객체감지"

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
    # 저화질 스트림이 있으면 그것을 쓴다. 끄면 원본을 쓴다 — 작은 물체를 잡아야 해서
    # 해상도가 필요한 모델이면 끈다.
    prefer_sub_stream: bool = True
    # 이 크기(sqrt(w*h), 픽셀)보다 작은 박스는 버린다. 0 이면 안 버린다.
    #
    # 사내 실험 기록: 작은 이물질(볼트·나사류)을 무시하려고 **라벨에서 지웠더니** 오히려
    # 성능이 떨어졌다(mAP 0.851 -> 0.737). 한쪽에서는 잡으라 하고 다른 쪽에서는 무시하라는
    # 모순된 감독이 되기 때문이다. 학습 신호는 보존하고 **여기서 걸러야** 한다.
    min_box_px: float = 0.0
    dry_run: bool = False

    # ── 학습·화면 (이 모듈만 갖는 것) ──
    serve_port: int = 8000               # 컨테이너 안에서 화면·API 를 띄우는 포트
    # 브라우저가 닿는 주소. 플랫폼이 이 주소를 탭으로 감싸 보여 준다.
    public_url: str = "http://localhost:11990"
    runs_dir: str = "/training/runs"     # 학습 산출물
    models_dir: str = "/models"          # 추론이 쓰는 모델과 사전학습 가중치
    # 데이터셋 yaml 을 찾을 곳. 라벨링·전처리는 이 모듈의 일이 아니다.
    dataset_dirs: tuple[str, ...] = ("/training/datasets", "/datasets")
    train_device: str = "0"              # 학습에 쓸 장치. GPU 번호 또는 cpu


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
    cfg.min_box_px = num("MIN_BOX_PX", 0.0)
    cfg.prefer_sub_stream = flag("PREFER_SUB_STREAM", True)
    cfg.dry_run = flag("DRY_RUN", False)

    cfg.serve_port = int(num("SERVE_PORT", 8000))
    cfg.public_url = env("PUBLIC_URL", "http://localhost:11990").rstrip("/")
    cfg.runs_dir = env("RUNS_DIR", "/training/runs")
    cfg.models_dir = env("MODELS_DIR", "/models")
    roots = env("DATASET_DIRS")
    if roots:
        cfg.dataset_dirs = tuple(p.strip() for p in roots.split(",") if p.strip())
    cfg.train_device = env("TRAIN_DEVICE", "0")

    if not cfg.dry_run and (cfg.model_path is None or not cfg.model_path.is_file()):
        # 죽이지 않고 dry-run 으로 내려앉는다. 모델이 아직 없는 환경에서 컨테이너가
        # 재시작을 반복하는 것보다, 배관이 도는 것을 보여 주는 편이 낫다.
        log.warning("모델 파일(%s)이 없습니다 — dry-run 으로 전환합니다", cfg.model_path)
        cfg.dry_run = True
    if not cfg.class_map:
        log.warning("CLASS_MAP 이 비어 있습니다 — 판정 결과를 항목 코드로 옮길 수 없습니다. "
                    "플랫폼에서 탐지 항목을 먼저 만들고 CLASS_MAP 을 채우세요.")
    return cfg

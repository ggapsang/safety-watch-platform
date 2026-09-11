"""이 모듈의 설정.

공통 항목(플랫폼 주소·브로커·발행 주기·판정 창)은 SDK 가 읽는다. 여기서는 모델과 관련된
것만 더한다. 모델이 바뀔 때 고쳐야 하는 것은 CLASS_MAP 하나뿐이다 — 모델의 클래스를
플랫폼의 탐지 항목 코드로 옮기는 표다. 코드는 건드리지 않는다.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, "/app")

import settings  # noqa: E402

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
    # 화면에서 추론을 꺼 둔 상태. dry_run 과 다르다 — dry_run 은 합성 박스를 발행하고,
    # 이것은 아무것도 발행하지 않는다(settings.Settings.stopped 주석 참조).
    stopped: bool = False

    # 모델 클래스 이름 -> 사람이 붙인 이름. 화면에 그려지는 것은 이 값이다.
    # class_map(항목 코드)과 층이 다르다 — 이름은 보여 주기용, 코드는 이벤트 승격용이라
    # 하나로 합치면 '이벤트로 안 올리지만 이름은 보고 싶은' 클래스를 표현할 수 없다.
    aliases: dict[str, str] = field(default_factory=dict)

    # ── 화면 (이 모듈만 갖는 것) ──
    serve_port: int = 8000               # 컨테이너 안에서 화면·API 를 띄우는 포트
    # 브라우저가 닿는 주소. 플랫폼이 이 주소를 탭으로 감싸 보여 준다.
    public_url: str = "http://localhost:11990"
    # 모델 파일과 이 모듈의 설정(module.json)이 같이 놓이는 곳. 볼륨째 옮기면 따라간다.
    models_dir: str = "/models"


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
    cfg.models_dir = env("MODELS_DIR", "/models")

    # 화면에서 고친 값이 env 를 이긴다. 파일이 없으면 env 그대로 — 화면에 한 번도
    # 들어가지 않은 현장도 그대로 돌아야 한다.
    apply_settings(cfg, settings.load(Path(cfg.models_dir)))

    if not cfg.dry_run and (cfg.model_path is None or not cfg.model_path.is_file()):
        # 죽이지 않고 dry-run 으로 내려앉는다. 모델이 아직 없는 환경에서 컨테이너가
        # 재시작을 반복하는 것보다, 배관이 도는 것을 보여 주는 편이 낫다.
        log.warning("모델 파일(%s)이 없습니다 — dry-run 으로 전환합니다. "
                    "모듈 화면(%s)에서 모델을 올리세요.", cfg.model_path, cfg.public_url)
        cfg.dry_run = True
    if not cfg.class_map:
        log.warning("클래스와 탐지 항목의 연결이 비어 있습니다 — 박스는 그리지만 이벤트는 "
                    "만들지 않습니다. 모듈 화면(%s)에서 연결하세요.", cfg.public_url)
    return cfg


def apply_settings(cfg: Config, s: "settings.Settings") -> None:
    """설정 파일의 값을 설정 객체에 덮어쓴다.

    기동할 때와, 화면에서 값을 고쳐 워커를 다시 띄울 때 같은 함수를 쓴다 — 두 경로가
    갈리면 '화면에서 바꾼 것과 재시작 후가 다른' 일이 생긴다.
    """
    for name in settings.TUNING:
        if name in s.tuning:
            setattr(cfg, name, s.tuning[name])

    cfg.stopped = s.stopped
    if s.active_model:
        candidate = Path(cfg.models_dir) / s.active_model
        if candidate.is_file():
            cfg.model_path = candidate
            cfg.dry_run = False
        else:
            log.warning("설정이 가리키는 모델이 없습니다: %s", candidate)

    model = cfg.model_path.name if cfg.model_path else ""
    if model and s.rows(model):
        # 화면에서 만든 표가 있으면 그것이 CLASS_MAP env 를 대신한다.
        cfg.class_map = s.class_map(model)
        cfg.aliases = s.alias_map(model)

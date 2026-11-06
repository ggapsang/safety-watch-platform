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
    # env 의 MODEL_PATH. 화면에서 한 번도 고르지 않은 현장의 기본값일 뿐이다.
    model_path: Path | None = None
    # 공통 모델들(파일이 있는 것만). 카메라별로 따로 고르지 않은 카메라가 전부 돌린다.
    common_models: list[Path] = field(default_factory=list)
    # env·CLI 로 dry-run 을 강제했나. 모델을 올리면 dry-run 이 풀리는데, 이것만은 남는다.
    force_dry_run: bool = False
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

    # 모델 클래스 이름 -> 사람이 붙인 이름. 화면에 그려지는 것은 이 값이다.
    # class_map(항목 코드)과 층이 다르다 — 이름은 보여 주기용, 코드는 이벤트 승격용이라
    # 하나로 합치면 '이벤트로 안 올리지만 이름은 보고 싶은' 클래스를 표현할 수 없다.
    aliases: dict[str, str] = field(default_factory=dict)
    # 카메라 번호(문자열) -> 그 카메라에 걸 모델 파일 이름들. 비면 공통 모델들을 쓴다.
    # 워커마다 모델이 다를 수 있어 경로 하나로 담을 수 없다 — 표를 넘기고 워커가 고른다.
    camera_models: dict[str, list[str]] = field(default_factory=dict)
    # 추론을 꺼 둔 카메라 번호(문자열). 이 카메라는 영상도 열지 않는다.
    camera_off: set[str] = field(default_factory=set)

    # 모델 이름 -> (class_map, aliases, 끈 클래스). 카메라별 모델을 쓸 때 각자의 표를 찾는 자리다.
    model_maps: dict[str, tuple[dict[str, str], dict[str, str], set[str]]] = field(
        default_factory=dict)

    def is_off(self, camera_id: int) -> bool:
        """이 카메라는 추론하지 않기로 했나."""
        return str(camera_id) in self.camera_off

    def models_for(self, camera_id: int) -> list[Path]:
        """이 카메라가 열 모델들. 따로 고르지 않았으면 공통 모델들(없으면 빈 목록)."""
        names = self.camera_models.get(str(camera_id)) or []
        if names:
            return [Path(self.models_dir) / n for n in names]
        return list(self.common_models)

    def models_in_use(self) -> set[str]:
        """어느 카메라에서든 돌 수 있는 모델 이름들."""
        return ({p.name for p in self.common_models}
                | {n for names in self.camera_models.values() for n in names})

    @property
    def capabilities(self) -> list[str]:
        """플랫폼에 '나는 이 항목들을 판정한다' 고 알리는 목록.

        쓰는 모델 **전부**의 표에서 모은다. 예전에는 공통 모델 하나의 표만 봐서, 카메라별로
        따로 건 모델이 내는 항목은 플랫폼이 모르는 채로 들어왔다.
        """
        items = set(self.class_map.values())                 # env 의 초기 표
        for name in self.models_in_use():
            items |= set(self.model_maps.get(name, ({}, {}, set()))[0].values())
        return sorted(items)

    def maps_for(self, model_name: str) -> tuple[dict[str, str], dict[str, str], set[str]]:
        """그 모델의 (항목 코드 표, 표시 이름 표, 끈 클래스).

        표가 없으면 공통 설정으로 내려간다 — 모델을 막 올려 아직 아무것도 연결하지 않은
        상태에서도 박스는 그려져야 한다(끈 클래스도 없다).
        """
        return self.model_maps.get(model_name, (self.class_map, self.aliases, set()))

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
    cfg.force_dry_run = cfg.dry_run = flag("DRY_RUN", False)

    cfg.serve_port = int(num("SERVE_PORT", 8000))
    cfg.public_url = env("PUBLIC_URL", "http://localhost:11990").rstrip("/")
    cfg.models_dir = env("MODELS_DIR", "/models")

    # 화면에서 고친 값이 env 를 이긴다. 파일이 없으면 env 그대로 — 화면에 한 번도
    # 들어가지 않은 현장도 그대로 돌아야 한다.
    apply_settings(cfg, settings.load(Path(cfg.models_dir)))

    if cfg.dry_run and not cfg.force_dry_run:
        # 죽이지 않고 dry-run 으로 내려앉는다. 모델이 아직 없는 환경에서 컨테이너가
        # 재시작을 반복하는 것보다, 배관이 도는 것을 보여 주는 편이 낫다.
        log.warning("모델 파일(%s)이 없습니다 — dry-run 으로 전환합니다. "
                    "모듈 화면(%s)에서 모델을 올리세요.", cfg.model_path, cfg.public_url)
    if not cfg.capabilities:
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

    cfg.camera_off = set(s.camera_off)
    # 카메라마다 다른 모델을, 여러 개까지 쓸 수 있다. 여기서는 표만 넘기고, 실제로 어느
    # 모델을 열지는 워커가 자기 카메라를 보고 정한다(main.YoloSource.open) — 워커마다
    # 모델이 다르므로 설정 객체 하나에 경로를 담을 수 없다.
    cfg.camera_models = {}
    dropped: list[str] = []
    for cam, names in s.camera_models.items():
        keep = [n for n in names if (Path(cfg.models_dir) / n).is_file()]
        dropped += [f"{cam}->{n}" for n in names if n not in keep]
        if keep:
            cfg.camera_models[cam] = keep
    if dropped:
        # 지정만 남고 파일이 사라진 경우다. 그 카메라를 멈추지 않고 남은 모델로 돈다 —
        # 모델 하나가 없어졌다고 카메라가 통째로 눈을 감으면 더 나쁘다.
        log.warning("카메라별 모델 지정 중 파일이 없는 것을 건너뜁니다: %s",
                    ", ".join(sorted(dropped)))

    # 공통 모델들. 한 번도 고르지 않았으면(None) env 의 MODEL_PATH 하나를 쓴다.
    if s.active_models is None:
        chosen = [cfg.model_path] if cfg.model_path else []
    else:
        chosen = [Path(cfg.models_dir) / n for n in s.active_models]
    cfg.common_models = [p for p in chosen if p.is_file()]
    for p in chosen:
        if not p.is_file():
            log.warning("공통 모델로 켜 둔 파일이 없습니다: %s", p)

    # dry-run 은 '모델이 아직 하나도 없는 새 현장' 에서만 내려앉는다. 일부러 다 껐으면
    # (active_models == []) 아무것도 하지 않아야 한다 — 그때 합성 박스를 내보내면 끈
    # 화면에 가짜 박스가 뜬다.
    if cfg.common_models or cfg.camera_models:
        cfg.dry_run = cfg.force_dry_run
    else:
        cfg.dry_run = cfg.force_dry_run or s.active_models is None

    # 올려 둔 모델 전부의 표를 담는다. 워커가 자기 모델의 표를 여기서 찾는다.
    cfg.model_maps = {name: (s.class_map(name), s.alias_map(name), s.disabled(name))
                      for name in s.models}

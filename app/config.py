"""환경변수 기반 설정.

상세기획안 10장: "카메라/모델/PLC/임계값 전부 환경변수로 주입(재빌드 없이 변경)".
`.env` 파일이 있으면 먼저 읽어 os.environ 에 채운 뒤 파싱한다(외부 의존성 없음).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: str | os.PathLike | None = None) -> None:
    """아주 단순한 .env 로더. 이미 설정된 환경변수는 덮어쓰지 않는다."""
    p = Path(path) if path else PROJECT_ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def _str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "").strip() or default)
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, "").strip() or default)
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _polygon(key: str) -> list[tuple[float, float]]:
    """ROI 폴리곤. "x1,y1;x2,y2;..." 형식. 정규화(0~1) 또는 픽셀 좌표 모두 허용."""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return []
    pts: list[tuple[float, float]] = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        x, _, y = chunk.partition(",")
        pts.append((float(x), float(y)))
    if len(pts) < 3:
        raise ValueError(f"{key}: 폴리곤은 점이 3개 이상이어야 합니다 (현재 {len(pts)}개)")
    return pts


@dataclass
class VideoConfig:
    # rtsp://user:pass@ip:554/... — 자격증명은 환경변수로만 관리(소스 하드코딩 금지)
    source: str = ""
    reconnect_sec: float = 3.0
    read_timeout_sec: float = 10.0
    # OpenCV FFmpeg 백엔드 옵션(지연 누적 방지)
    ffmpeg_options: str = "rtsp_transport;tcp|buffer_size;1024000|stimeout;5000000"


@dataclass
class ModelConfig:
    backend: str = "onnx"                 # onnx | torchscript
    onnx_path: str = "model_files/best_trash.torchscript.onnx"
    torchscript_path: str = "model_files/best_trash.torchscript.pt"
    imgsz: int = 640
    device: str = "cuda"                  # cuda | cpu (cuda 불가 시 자동 cpu 폴백)
    fp16: bool = False
    # decoded | raw | auto — 첨부 모델은 그래프 내 grid-decode+sigmoid 완료 → decoded 확정.
    infer_mode: str = "decoded"
    letterbox: bool = True                # False 면 stretch(카메라 앱 방식)
    conf_threshold: float = 0.25
    iou_threshold: float = 0.45
    max_det: int = 300


@dataclass
class LogicConfig:
    window_sec: float = 2.0
    count_low: int = 1
    count_high: int = 2
    # ⚠ area_ratio 단위는 '퍼센트(%)'. (bbox 총면적 / ROI 또는 프레임 면적) * 100.
    #   기본 임계값 0.3 / 3.0 은 0.3% / 3% 를 뜻한다. AREA_UNIT=ratio 로 바꾸면 0~1 비율.
    area_low: float = 0.3
    area_high: float = 3.0
    area_unit: str = "percent"            # percent | ratio
    cooldown_sec: float = 8.0             # 최소 1초
    # IF 맵상 0 은 '정회전'이라는 능동 명령이다. 정상 복귀 시 0 을 써야 라인이 되살아난다.
    write_zero_on_normal: bool = True
    roi_polygon: list[tuple[float, float]] = field(default_factory=list)
    # 판정실패(카메라/추론 오류) 코드. 영상이 fault_after_sec 이상 끊기면 1회 전송.
    fault_code: int = 9
    fault_after_sec: float = 5.0


@dataclass
class PlcConfig:
    """IF 맵(Rev. 2026.07.03) 기준 — PC 는 write 만 한다. D8000/D8001 읽기는 사용하지 않는다."""

    enabled: bool = True
    host: str = "192.168.5.199"
    port: int = 2004                      # XGT 전용 프로토콜
    cpu_info: int = 0x00                  # XGB 계열은 CPU_ANY 권장
    timeout_sec: float = 2.0
    addr_pc_ready: str = "%DW8100"        # PC Ready       0=Off / 1=On
    addr_result: str = "%DW8101"          # 판정 결과 코드  0/1/2/9


@dataclass
class DashboardConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8080
    jpeg_quality: int = 75
    stream_fps: float = 15.0


@dataclass
class AppConfig:
    video: VideoConfig = field(default_factory=VideoConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    logic: LogicConfig = field(default_factory=LogicConfig)
    plc: PlcConfig = field(default_factory=PlcConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv()
        cfg = cls(
            video=VideoConfig(
                source=_str("VIDEO_SOURCE", ""),
                reconnect_sec=_float("VIDEO_RECONNECT_SEC", 3.0),
                read_timeout_sec=_float("VIDEO_READ_TIMEOUT_SEC", 10.0),
                ffmpeg_options=_str("VIDEO_FFMPEG_OPTIONS", VideoConfig.ffmpeg_options),
            ),
            model=ModelConfig(
                backend=_str("MODEL_BACKEND", "onnx").lower(),
                onnx_path=_str("MODEL_ONNX_PATH", ModelConfig.onnx_path),
                torchscript_path=_str("MODEL_TORCHSCRIPT_PATH", ModelConfig.torchscript_path),
                imgsz=_int("MODEL_IMGSZ", 640),
                device=_str("MODEL_DEVICE", "cuda").lower(),
                fp16=_bool("MODEL_FP16", False),
                infer_mode=_str("MODEL_INFER_MODE", "decoded").lower(),
                letterbox=_bool("MODEL_LETTERBOX", True),
                conf_threshold=_float("CONF_THRESHOLD", 0.25),
                iou_threshold=_float("IOU_THRESHOLD", 0.45),
                max_det=_int("MAX_DET", 300),
            ),
            logic=LogicConfig(
                window_sec=_float("WINDOW_SEC", 2.0),
                count_low=_int("COUNT_LOW", 1),
                count_high=_int("COUNT_HIGH", 2),
                area_low=_float("AREA_LOW", 0.3),
                area_high=_float("AREA_HIGH", 3.0),
                area_unit=_str("AREA_UNIT", "percent").lower(),
                cooldown_sec=_float("COOLDOWN_SEC", 8.0),
                write_zero_on_normal=_bool("WRITE_ZERO_ON_NORMAL", True),
                roi_polygon=_polygon("ROI_POLYGON"),
                fault_code=_int("FAULT_CODE", 9),
                fault_after_sec=_float("FAULT_AFTER_SEC", 5.0),
            ),
            plc=PlcConfig(
                enabled=_bool("PLC_ENABLED", True),
                host=_str("PLC_HOST", PlcConfig.host),
                port=_int("PLC_PORT", 2004),
                cpu_info=_int("PLC_CPU_INFO", 0),
                timeout_sec=_float("PLC_TIMEOUT_SEC", 2.0),
                addr_pc_ready=_str("PLC_ADDR_PC_READY", "%DW8100"),
                addr_result=_str("PLC_ADDR_RESULT", "%DW8101"),
            ),
            dashboard=DashboardConfig(
                enabled=_bool("DASHBOARD_ENABLED", True),
                host=_str("DASHBOARD_HOST", "0.0.0.0"),
                port=_int("DASHBOARD_PORT", 8080),
                jpeg_quality=_int("DASHBOARD_JPEG_QUALITY", 75),
                stream_fps=_float("DASHBOARD_STREAM_FPS", 15.0),
            ),
            log_level=_str("LOG_LEVEL", "INFO").upper(),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.model.backend not in ("onnx", "torchscript"):
            raise ValueError(f"MODEL_BACKEND 는 onnx | torchscript 여야 합니다: {self.model.backend}")
        if self.model.infer_mode not in ("decoded", "raw", "auto"):
            raise ValueError(f"MODEL_INFER_MODE 는 decoded | raw | auto 여야 합니다: {self.model.infer_mode}")
        if self.logic.area_unit not in ("percent", "ratio"):
            raise ValueError(f"AREA_UNIT 는 percent | ratio 여야 합니다: {self.logic.area_unit}")
        if self.logic.count_low > self.logic.count_high:
            raise ValueError("COUNT_LOW 는 COUNT_HIGH 이하여야 합니다")
        if self.logic.area_low > self.logic.area_high:
            raise ValueError("AREA_LOW 는 AREA_HIGH 이하여야 합니다")
        # 설계 문서: cooldown 최소 1초
        self.logic.cooldown_sec = max(1.0, self.logic.cooldown_sec)

    def model_path(self) -> Path:
        rel = self.model.onnx_path if self.model.backend == "onnx" else self.model.torchscript_path
        p = Path(rel)
        return p if p.is_absolute() else (PROJECT_ROOT / p)

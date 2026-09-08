"""추론 — 백엔드 추상화(ONNX Runtime / TorchScript) + 전·후처리.

첨부 모델(`best_trash.torchscript.onnx`) 그래프 검증 결과:
  input  : `images`  float32 [1, 3, 640, 640]           -> /255 정규화 필요, 배치 1 고정
  Detect : model.105 에 Sigmoid + grid decode 포함      -> Concat_6 = [1, 25200, 9] (nc=4)
  tail   : Slice([..., :5]) + ReduceMax(4클래스, keepdims) -> Concat(axis=-1)
  output : `output`  float32 [1, 25200, 6] = [cx, cy, w, h, obj, cls]
           좌표는 **모델 입력(640) 픽셀 좌표계**, obj/cls 는 sigmoid 적용 완료.

=> infer_mode 는 **decoded** 가 맞다. 4물체 -> trash 단일 라벨 통일도 ReduceMax 로 그래프에
   이미 들어가 있으므로 앱은 단일 클래스로만 처리한다.
   (raw 경로는 향후 다른 export 파일 대비 fallback 으로 남겨둔다.)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# YOLOv7-p5 기본 앵커 (raw 모드 fallback 전용)
DEFAULT_ANCHORS = np.array([
    [[12, 16], [19, 36], [40, 28]],        # P3/8
    [[36, 75], [76, 55], [72, 146]],       # P4/16
    [[142, 110], [192, 243], [459, 401]],  # P5/32
], dtype=np.float32)
DEFAULT_STRIDES = (8, 16, 32)


@dataclass
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    score: float              # 최종 confidence = obj * cls
    obj: float = 0.0          # objectness (sigmoid 적용됨)
    cls: float = 0.0          # 클래스 점수 = 4클래스의 max (그래프의 ReduceMax 결과)
    raw_scores: list[float] = field(default_factory=list)   # 후처리 전 4클래스 점수
    raw_label: str = ""       # 그 중 argmax 라벨 — '원래 어떤 물체로 봤는지'

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)


# --------------------------------------------------------------------- 전처리


def letterbox(img: np.ndarray, new_size: int = 640,
              color: tuple[int, int, int] = (114, 114, 114)) -> tuple[np.ndarray, float, tuple[float, float]]:
    """비율 유지 + 패딩. 반환: (resized, ratio, (dw, dh))."""
    import cv2

    h, w = img.shape[:2]
    ratio = min(new_size / h, new_size / w)
    nh, nw = int(round(h * ratio)), int(round(w * ratio))
    if (nw, nh) != (w, h):
        interp = cv2.INTER_LINEAR if ratio > 1 else cv2.INTER_AREA
        img = cv2.resize(img, (nw, nh), interpolation=interp)
    dw, dh = (new_size - nw) / 2.0, (new_size - nh) / 2.0
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
    return img, ratio, (left, top)


def stretch_resize(img: np.ndarray, new_size: int = 640) -> tuple[np.ndarray, tuple[float, float]]:
    """비율 무시 stretch (카메라 .nb 앱과 동일 방식). 반환: (resized, (rx, ry))."""
    import cv2

    h, w = img.shape[:2]
    out = cv2.resize(img, (new_size, new_size), interpolation=cv2.INTER_LINEAR)
    return out, (new_size / w, new_size / h)


# --------------------------------------------------------------------- 후처리


def xywh2xyxy(boxes: np.ndarray) -> np.ndarray:
    out = np.empty_like(boxes)
    out[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    out[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    out[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    out[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    return out


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """단일 클래스 IoU NMS (numpy)."""
    if boxes.size == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        iou = inter / np.maximum(1e-9, areas[i] + areas[rest] - inter)
        order = rest[iou <= iou_threshold]
    return keep


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def decode_raw(pred: np.ndarray, imgsz: int = 640,
               anchors: np.ndarray = DEFAULT_ANCHORS,
               strides: tuple[int, ...] = DEFAULT_STRIDES) -> np.ndarray:
    """raw logit [N, 5+nc] -> 디코딩된 [N, 5+nc] (입력 픽셀 좌표, sigmoid 적용).

    yolov7 Detect 레이어와 동일: 앵커-major -> row -> col 순서로 concat 되어 있다고 가정.
    """
    out = []
    offset = 0
    for level, stride in enumerate(strides):
        ny = nx = imgsz // stride
        na = anchors.shape[1]
        n = na * ny * nx
        block = _sigmoid(pred[offset:offset + n].astype(np.float32)).reshape(na, ny, nx, -1)
        offset += n

        grid_y, grid_x = np.meshgrid(np.arange(ny, dtype=np.float32),
                                     np.arange(nx, dtype=np.float32), indexing="ij")
        grid = np.stack((grid_x, grid_y), axis=-1)[None]                 # (1, ny, nx, 2)
        anchor_wh = anchors[level].reshape(na, 1, 1, 2).astype(np.float32)

        block[..., 0:2] = (block[..., 0:2] * 2.0 - 0.5 + grid) * stride
        block[..., 2:4] = (block[..., 2:4] * 2.0) ** 2 * anchor_wh
        out.append(block.reshape(n, -1))
    if offset != pred.shape[0]:
        raise ValueError(f"raw decode 크기 불일치: {offset} != {pred.shape[0]}")
    return np.concatenate(out, axis=0)


def point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    """ray casting."""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1
            if x < xin:
                inside = not inside
    return inside


def polygon_area(poly: list[tuple[float, float]]) -> float:
    """Shoelace."""
    n = len(poly)
    s = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def resolve_roi(poly: list[tuple[float, float]], width: int, height: int) -> list[tuple[float, float]]:
    """정규화(0~1) 좌표면 픽셀로 변환. 이미 픽셀이면 그대로."""
    if not poly:
        return []
    if all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in poly):
        return [(x * width, y * height) for x, y in poly]
    return list(poly)


# --------------------------------------------------------------------- 백엔드


class _Backend:
    input_name: str = ""
    input_dtype: np.dtype = np.dtype(np.float32)
    device: str = "cpu"
    has_raw: bool = False

    def infer(self, blob: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
        """(최종 출력, 후처리 전 4클래스 텐서 or None)"""
        raise NotImplementedError


def _model_bytes_with_raw_output(path: Path, raw_name: str) -> bytes | None:
    """ReduceMax 직전 텐서를 그래프 출력에 추가한 모델 바이트를 만든다.

    실패하면 None 을 반환하고 호출자가 원본 경로로 폴백한다.
    """
    try:
        import onnx
    except ImportError:
        log.warning("onnx 미설치 — 후처리 전 4클래스 라벨 노출을 건너뜁니다")
        return None
    try:
        model = onnx.load(str(path))
        if any(o.name == raw_name for o in model.graph.output):
            return model.SerializeToString()
        vi = next((v for v in model.graph.value_info if v.name == raw_name), None)
        if vi is None:
            log.warning("그래프에 '%s' 텐서가 없습니다 — 4클래스 노출 생략", raw_name)
            return None
        model.graph.output.append(vi)
        return model.SerializeToString()
    except Exception as exc:                                   # noqa: BLE001
        log.warning("4클래스 텐서 노출 실패(%s) — 원본 모델로 진행", exc)
        return None


class OnnxBackend(_Backend):
    def __init__(self, path: Path, device: str = "cuda", expose_raw: bool = False,
                 raw_tensor_name: str = "") -> None:
        import onnxruntime as ort

        available = ort.get_available_providers()
        providers = ["CPUExecutionProvider"]
        if device == "cuda" and "CUDAExecutionProvider" in available:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        elif device == "cuda":
            log.warning("CUDAExecutionProvider 사용 불가 -> CPU 폴백 (설치된 provider: %s)", available)

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        source: str | bytes = str(path)
        if expose_raw and raw_tensor_name:
            patched = _model_bytes_with_raw_output(path, raw_tensor_name)
            if patched is not None:
                source = patched

        self.session = ort.InferenceSession(source, sess_options=so, providers=providers)
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.input_dtype = np.dtype(np.float16) if "float16" in inp.type else np.dtype(np.float32)
        outs = self.session.get_outputs()
        self.output_name = outs[0].name
        self.raw_name = raw_tensor_name if any(o.name == raw_tensor_name for o in outs) else ""
        self.has_raw = bool(self.raw_name)
        self.device = "cuda" if "CUDAExecutionProvider" in self.session.get_providers() else "cpu"
        log.info("ONNX 백엔드: providers=%s input=%s%s output=%s raw=%s",
                 self.session.get_providers(), inp.name, inp.shape, self.output_name,
                 self.raw_name or "미노출")

    def infer(self, blob: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
        names = [self.output_name] + ([self.raw_name] if self.has_raw else [])
        outs = self.session.run(names, {self.input_name: blob})
        return outs[0], (outs[1] if self.has_raw else None)


class TorchScriptBackend(_Backend):
    def __init__(self, path: Path, device: str = "cuda", fp16: bool = False) -> None:
        import torch

        self.torch = torch
        use_cuda = device == "cuda" and torch.cuda.is_available()
        if device == "cuda" and not use_cuda:
            log.warning("torch.cuda 사용 불가 -> CPU 폴백")
        self.device = "cuda" if use_cuda else "cpu"
        self.fp16 = fp16 and use_cuda
        self.model = torch.jit.load(str(path), map_location=self.device)
        self.model.eval()
        if self.fp16:
            self.model.half()
        self.input_name = "images"
        self.input_dtype = np.dtype(np.float16) if self.fp16 else np.dtype(np.float32)
        log.info("TorchScript 백엔드: device=%s fp16=%s", self.device, self.fp16)

    def infer(self, blob: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
        torch = self.torch
        with torch.no_grad():
            tensor = torch.from_numpy(blob).to(self.device)
            out = self.model(tensor)
            if isinstance(out, (tuple, list)):
                out = out[0]
            return out.detach().float().cpu().numpy(), None


# --------------------------------------------------------------------- Detector


class Detector:
    """전처리 -> 백엔드 추론 -> 후처리(conf/NMS/좌표 역변환/ROI) 를 담당."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        m = cfg.model
        path = cfg.model_path()
        if not path.exists():
            raise FileNotFoundError(f"모델 파일이 없습니다: {path}")

        if m.backend == "onnx":
            self.backend: _Backend = OnnxBackend(path, m.device, m.expose_raw_classes,
                                                 m.raw_tensor_name)
        else:
            self.backend = TorchScriptBackend(path, m.device, m.fp16)

        self.imgsz = m.imgsz
        self.infer_mode = m.infer_mode
        self._diagnosed = False
        self.last_infer_ms = 0.0

    def class_name(self, index: int) -> str:
        names = self.cfg.model.class_names
        return names[index] if index < len(names) else f"cls{index}"

    # ------------------------------------------------------------- 진단 로그

    def _diagnose(self, pred: np.ndarray) -> None:
        """최초 1회 출력 shape/샘플 로그 — decoded/raw 확정용 (상세기획안 11장)."""
        self._diagnosed = True
        log.info("[진단] 모델 출력 shape=%s dtype=%s", pred.shape, pred.dtype)
        flat = pred.reshape(-1, pred.shape[-1])
        sample = flat[: min(3, flat.shape[0])]
        log.info("[진단] 출력 샘플(앞 3행)=%s", np.array2string(sample, precision=4, suppress_small=True))
        xy_max = float(np.max(np.abs(flat[:, :4])))
        conf_min, conf_max = float(flat[:, 4].min()), float(flat[:, 4].max())
        looks_decoded = xy_max > 2.0 and 0.0 <= conf_min and conf_max <= 1.0
        log.info("[진단] |xywh|max=%.2f obj범위=[%.4f, %.4f] -> %s 로 보임",
                 xy_max, conf_min, conf_max, "decoded" if looks_decoded else "raw")
        if self.infer_mode == "auto":
            self.infer_mode = "decoded" if looks_decoded else "raw"
            log.info("[진단] infer_mode=auto -> '%s' 로 확정", self.infer_mode)
        elif (self.infer_mode == "decoded") != looks_decoded:
            log.warning("[진단] 설정된 infer_mode='%s' 가 출력 형태와 다를 수 있습니다. "
                        "박스 위치가 어긋나면 MODEL_INFER_MODE 를 먼저 의심하세요.", self.infer_mode)

    # ------------------------------------------------------------- 메인 경로

    def preprocess(self, frame: np.ndarray):
        import cv2

        if self.cfg.model.letterbox:
            img, ratio, pad = letterbox(frame, self.imgsz)
            meta = ("letterbox", ratio, pad)
        else:
            img, scale = stretch_resize(frame, self.imgsz)
            meta = ("stretch", scale, (0.0, 0.0))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        blob = img.transpose(2, 0, 1)[None]
        if self.backend.input_dtype == np.uint8:
            blob = np.ascontiguousarray(blob, dtype=np.uint8)      # NPU 계열: 정규화 생략
        else:
            blob = np.ascontiguousarray(blob, dtype=self.backend.input_dtype) / np.array(
                255.0, dtype=self.backend.input_dtype)
        return blob, meta

    def _scale_back(self, boxes: np.ndarray, meta, width: int, height: int) -> np.ndarray:
        kind, a, pad = meta
        if kind == "letterbox":
            boxes[:, [0, 2]] -= pad[0]
            boxes[:, [1, 3]] -= pad[1]
            boxes /= a
        else:
            rx, ry = a
            boxes[:, [0, 2]] /= rx
            boxes[:, [1, 3]] /= ry
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height)
        return boxes

    def detect(self, frame: np.ndarray) -> list[Detection]:
        h, w = frame.shape[:2]
        blob, meta = self.preprocess(frame)

        t0 = time.perf_counter()
        pred, raw = self.backend.infer(blob)
        self.last_infer_ms = (time.perf_counter() - t0) * 1000.0

        pred = np.asarray(pred, dtype=np.float32)
        if not self._diagnosed:
            self._diagnose(pred)

        flat = pred.reshape(-1, pred.shape[-1])
        if self.infer_mode == "raw":
            flat = decode_raw(flat, self.imgsz)
        raw_flat = np.asarray(raw, dtype=np.float32).reshape(-1, raw.shape[-1]) \
            if raw is not None else None

        obj = flat[:, 4]
        cls = flat[:, 5] if flat.shape[1] > 5 else np.ones_like(obj)
        scores = obj * cls                       # 단일 클래스(trash) — cls 는 4클래스의 max

        conf_t = self.cfg.model.conf_threshold
        keep_mask = scores >= conf_t
        if not np.any(keep_mask):
            return []
        # 원본 행 번호를 끝까지 들고 가야 raw 텐서에서 4클래스 점수를 찾을 수 있다.
        rows = np.nonzero(keep_mask)[0]
        boxes = xywh2xyxy(flat[rows, :4].copy())
        kept_scores = scores[rows]

        keep = nms(boxes, kept_scores, self.cfg.model.iou_threshold)[: self.cfg.model.max_det]
        rows = rows[keep]
        boxes = self._scale_back(boxes[keep], meta, w, h)
        kept_scores = kept_scores[keep]

        roi = resolve_roi(self.cfg.logic.roi_polygon, w, h)
        dets: list[Detection] = []
        for (x1, y1, x2, y2), s, row in zip(boxes, kept_scores, rows):
            if roi:
                cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                if not point_in_polygon(cx, cy, roi):
                    continue                     # 컨베이어 영역 밖 오탐 제거
            det = Detection(float(x1), float(y1), float(x2), float(y2), float(s),
                            obj=float(obj[row]), cls=float(cls[row]))
            if raw_flat is not None and raw_flat.shape[1] > 5:
                per_class = raw_flat[row, 5:]
                det.raw_scores = [float(v) for v in per_class]
                det.raw_label = self.class_name(int(per_class.argmax()))
            dets.append(det)
        return dets


def area_metric(dets: list[Detection], width: int, height: int,
                roi: list[tuple[float, float]], unit: str = "percent") -> float:
    """Σbbox면적 / (ROI 또는 프레임 면적). unit='percent' 면 100 을 곱한다."""
    denom = polygon_area(roi) if roi else float(width * height)
    if denom <= 0:
        return 0.0
    ratio = sum(d.area for d in dets) / denom
    return ratio * 100.0 if unit == "percent" else ratio

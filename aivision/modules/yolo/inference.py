"""전처리 -> 백엔드 -> 후처리.

전처리(letterbox)와 NMS 는 `refs/legacy-iseco-pc/app/detector.py` 에서 그대로 가져왔다.
검증된 코드를 다시 쓰는 편이 낫다.

후처리는 다시 썼다. 레거시는 4클래스를 그래프 안 ReduceMax 로 뭉갠 단일 클래스 전용
export 에 맞춰져 있었고 yolov7 앵커가 하드코딩돼 있었다. 여기서는 흔한 출력 모양 셋을
런타임에 판별한다 — 모델이 바뀔 때 코드를 고치지 않기 위해서다.

  (1, N, 5+nc)   yolov5/v7 계열: cx cy w h obj cls...
  (1, 4+nc, N)   yolov8 계열: cx cy w h cls...        (전치되어 나옴, obj 없음)
  (1, N, 6)      이미 NMS 까지 된 export: x1 y1 x2 y2 score cls

좌표는 0~1 정규화로 통일해 내보낸다. 플랫폼의 유일한 저장 규약이다. 픽셀로 보내고
boxes_format 으로 알려 주는 길도 있지만, 프레임 크기가 스트림 프로파일에 따라 바뀌므로
보내는 쪽에서 나누는 편이 안전하다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Detection:
    x1: float                # 아래 넷은 모두 0~1 정규화
    y1: float
    x2: float
    y2: float
    score: float
    cls: int
    label: str = ""

    def to_box(self) -> dict:
        """플랫폼 박스 모양. mapping.to_boxes(xyxy_norm) 가 읽는 키다."""
        return {"x1": round(self.x1, 4), "y1": round(self.y1, 4),
                "x2": round(self.x2, 4), "y2": round(self.y2, 4),
                "label": self.label, "score": round(self.score, 3)}


# ────────────────────────────────────────────────────────────── 전처리

def letterbox(img: np.ndarray, new_size: int = 640,
              color: tuple[int, int, int] = (114, 114, 114)):
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


# ────────────────────────────────────────────────────────────── 후처리

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


def _flatten(pred: np.ndarray) -> np.ndarray:
    """(1, A, B) -> (A, B). 배치 1 만 다룬다."""
    pred = np.asarray(pred, dtype=np.float32)
    if pred.ndim == 3:
        pred = pred[0]
    if pred.ndim != 2:
        raise ValueError(f"다룰 수 없는 출력 모양: {pred.shape}")
    return pred


def _as_candidates(pred: np.ndarray, nc: int = 0,
                   layout: str = "auto") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """출력 모양을 판별해 (xyxy, score, cls) 로 통일한다.

    nc      클래스 수. 모델 메타데이터에서 알 수 있으면 넘긴다 — 열 수로 확정할 수 있어
            추측이 필요 없어진다.
    layout  auto | v5 | v8 | nms. 자동 판별이 틀리는 모델을 만나면 환경변수로 못 박는다.
            판별에 기대는 코드는 언젠가 틀리므로 사람이 덮어쓸 길을 남겨 둔다.

    좌표는 아직 letterbox 좌표계(입력 크기 기준)다. 되돌리기는 _scale_back 이 한다.
    """
    pred = _flatten(pred)
    rows, cols = pred.shape

    # (4+nc, N) 로 전치되어 나오는 yolov8 계열.
    #
    # 판별 근거: 전치된 배치에서는 행이 '채널'(4+클래스수)이고 열이 '후보 수'다. 채널은
    # 많아도 수백이고 후보는 보통 수천이라 열이 행보다 압도적으로 많다. 배수(4배)까지 요구해
    # 애매한 작은 텐서를 전치하지 않도록 한다 — 잘못 전치하면 좌표가 통째로 뒤집힌다.
    transposed = False
    if 5 <= rows <= 300 and cols > rows * 4:
        pred = pred.T
        rows, cols = pred.shape
        transposed = True

    # 열이 6 개면 보통 NMS 까지 끝난 export 다. 다만 클래스가 1~2개인 모델의 원시 출력도
    # 6열이라 겹친다. 셋으로 가른다.
    #   · 전치했다면 채널 우선 배치였다는 뜻이니 원시 출력이 확실하다
    #   · 클래스 수를 알고 열 수가 맞으면 원시 출력이다
    #   · 후보가 수천 개면 NMS 를 지난 결과일 수 없다 (보통 수백 이하로 남는다)
    ambiguous = (transposed or rows > 1000
                 or (bool(nc) and cols in (5 + nc, 4 + nc)))
    if layout == "nms" or (layout == "auto" and cols == 6 and not ambiguous):
        # 이미 NMS 까지 끝난 export. 그대로 쓴다.
        return pred[:, :4], pred[:, 4], pred[:, 5].astype(int)

    if cols < 5:
        raise ValueError(f"열이 {cols} 개인 출력은 다룰 수 없습니다")

    boxes = xywh2xyxy(pred[:, :4])
    if _is_v8(nc, cols, transposed, layout):
        cls_scores = pred[:, 4:]
        obj = np.ones(rows, dtype=np.float32)
    else:
        obj = pred[:, 4]
        cls_scores = pred[:, 5:] if cols > 5 else pred[:, 4:5]

    cls = cls_scores.argmax(axis=1).astype(int)
    score = obj * cls_scores[np.arange(rows), cls]
    return boxes, score, cls


def _is_v8(nc: int, cols: int, transposed: bool, layout: str) -> bool:
    """objectness 열이 없는 배치(v8 계열)인가.

    확실한 근거부터 순서대로 쓴다. 값을 보고 추측하지 않는다 — 후보 대부분이 0 에 가까운
    텐서에서는 어떤 값 기반 판별도 신뢰할 수 없다.

      1. 사람이 LAYOUT 으로 지정했으면 그대로 따른다
      2. 클래스 수를 알면 열 수로 확정된다 (5+nc = obj 있음, 4+nc = obj 없음)
      3. 배치 방향으로 본다. ultralytics v8/v11 은 (1, 4+nc, N) 채널 우선으로 내보내고
         v5/v7 은 (1, N, 5+nc) 후보 우선으로 내보낸다. 전치가 필요했다는 것 자체가
         채널 우선이었다는 뜻이므로 v8 로 본다.

    2번이 가장 강하다. 그래서 load() 가 클래스 이름을 먼저 읽는다.
    """
    if layout == "v8":
        return True
    if layout == "v5":
        return False
    if nc:
        if cols == 5 + nc:
            return False
        if cols == 4 + nc:
            return True
        log.warning("클래스 수(%d)와 출력 열 수(%d)가 맞지 않습니다 — 배치 방향으로 "
                    "판별합니다. 틀리면 LAYOUT=v5 또는 v8 로 못 박으세요", nc, cols)
    return transposed


def _scale_back(box, ratio: float, pad, width: int, height: int):
    """letterbox 좌표 -> 원본 픽셀 -> 0~1 정규화."""
    x1, y1, x2, y2 = box
    dw, dh = pad
    r = max(ratio, 1e-9)
    x1 = (x1 - dw) / r / max(width, 1)
    x2 = (x2 - dw) / r / max(width, 1)
    y1 = (y1 - dh) / r / max(height, 1)
    y2 = (y2 - dh) / r / max(height, 1)
    clamp = lambda v: max(0.0, min(1.0, float(v)))          # noqa: E731
    return clamp(x1), clamp(y1), clamp(x2), clamp(y2)


# ────────────────────────────────────────────────────────────── 백엔드

class _Backend:
    input_dtype: np.dtype = np.dtype(np.float32)
    device: str = "cpu"

    def infer(self, blob: np.ndarray) -> np.ndarray:
        raise NotImplementedError


class OnnxBackend(_Backend):
    def __init__(self, path: Path, device: str = "cuda") -> None:
        import onnxruntime as ort

        available = ort.get_available_providers()
        providers = ["CPUExecutionProvider"]
        if device == "cuda" and "CUDAExecutionProvider" in available:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        elif device == "cuda":
            log.warning("CUDAExecutionProvider 사용 불가 -> CPU 폴백 (설치된 provider: %s)",
                        available)

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(path), sess_options=so, providers=providers)

        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.input_dtype = (np.dtype(np.float16) if "float16" in inp.type
                            else np.dtype(np.float32))
        self.output_name = self.session.get_outputs()[0].name
        self.device = ("cuda" if "CUDAExecutionProvider" in self.session.get_providers()
                       else "cpu")
        log.info("ONNX 백엔드: device=%s input=%s%s output=%s",
                 self.device, inp.name, inp.shape, self.output_name)

    def infer(self, blob: np.ndarray) -> np.ndarray:
        return self.session.run([self.output_name], {self.input_name: blob})[0]

    def metadata_names(self) -> list[str]:
        """export 가 클래스 이름을 메타데이터에 심어 두는 경우가 많다."""
        meta = self.session.get_modelmeta().custom_metadata_map or {}
        raw = meta.get("names") or ""
        if not raw:
            return []
        try:
            import ast

            data = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return []
        if isinstance(data, dict):
            try:
                return [str(data[k]) for k in sorted(data, key=lambda x: int(x))]
            except (TypeError, ValueError):
                return [str(v) for v in data.values()]
        if isinstance(data, list):
            return [str(x) for x in data]
        return []


class TorchScriptBackend(_Backend):
    def __init__(self, path: Path, device: str = "cuda") -> None:
        import torch

        self.torch = torch
        use_cuda = device == "cuda" and torch.cuda.is_available()
        if device == "cuda" and not use_cuda:
            log.warning("torch.cuda 사용 불가 -> CPU 폴백")
        self.device = "cuda" if use_cuda else "cpu"
        self.model = torch.jit.load(str(path), map_location=self.device)
        self.model.eval()
        log.info("TorchScript 백엔드: device=%s", self.device)

    def infer(self, blob: np.ndarray) -> np.ndarray:
        torch = self.torch
        with torch.no_grad():
            out = self.model(torch.from_numpy(blob).to(self.device))
            if isinstance(out, (tuple, list)):
                out = out[0]
            return out.detach().float().cpu().numpy()


def _load_backend(path: Path, device: str) -> _Backend:
    suffix = path.suffix.lower()
    if suffix == ".onnx":
        return OnnxBackend(path, device)
    if suffix in (".pt", ".torchscript"):
        return TorchScriptBackend(path, device)
    raise ValueError(f"지원하지 않는 모델 형식입니다: {path.name} (.onnx 또는 .torchscript)")


# ────────────────────────────────────────────────────────────── 모델

class Model:
    """모델 하나. 스레드 하나가 소유한다(ONNX 세션을 여러 스레드에서 공유하지 않는다)."""

    def __init__(self, path: Path, device: str = "cuda", imgsz: int = 640,
                 conf_thres: float = 0.25, iou_thres: float = 0.45,
                 class_map: dict[str, str] | None = None,
                 layout: str = "auto") -> None:
        self.layout = layout
        self.imgsz = imgsz
        self.conf_thres = conf_thres
        self.iou_thres = iou_thres
        self.class_map = class_map or {}
        self.names: list[str] = []
        self.backend = _load_backend(path, device)
        self.device = self.backend.device

    def class_name(self, index: int) -> str:
        if 0 <= index < len(self.names):
            return self.names[index]
        return str(index)

    def item_code(self, index: int) -> str:
        """모델 클래스 -> 플랫폼 탐지 항목 코드. 없으면 빈 문자열(무시 대상)."""
        return (self.class_map.get(self.class_name(index))
                or self.class_map.get(str(index)) or "")

    def infer(self, frame: np.ndarray) -> list[Detection]:
        """BGR 프레임 하나 -> 정규화 박스 목록."""
        h, w = frame.shape[:2]
        blob, ratio, pad = self._preprocess(frame)
        boxes, scores, classes = _as_candidates(
            self.backend.infer(blob), nc=len(self.names), layout=self.layout)

        keep = scores >= self.conf_thres
        boxes, scores, classes = boxes[keep], scores[keep], classes[keep]
        if boxes.size == 0:
            return []

        out: list[Detection] = []
        for c in np.unique(classes):
            sel = classes == c
            idx = nms(boxes[sel], scores[sel], self.iou_thres)
            if not idx:
                continue
            for (x1, y1, x2, y2), s in zip(boxes[sel][idx], scores[sel][idx]):
                nx1, ny1, nx2, ny2 = _scale_back((x1, y1, x2, y2), ratio, pad, w, h)
                out.append(Detection(nx1, ny1, nx2, ny2, float(s), int(c),
                                     self.class_name(int(c))))
        return out

    def _preprocess(self, frame: np.ndarray):
        import cv2

        img, ratio, pad = letterbox(frame, self.imgsz)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        blob = img.transpose(2, 0, 1)[None] / 255.0
        return np.ascontiguousarray(blob, dtype=self.backend.input_dtype), ratio, pad


def load(path: Path, device: str, imgsz: int, conf_thres: float, iou_thres: float,
         class_map: dict[str, str], layout: str = "auto") -> Model:
    model = Model(path, device, imgsz, conf_thres, iou_thres, class_map, layout)
    if isinstance(model.backend, OnnxBackend):
        # 클래스 이름을 여기서 먼저 읽는다 — 이름 수가 출력 열 해석을 확정해 준다.
        model.names = model.backend.metadata_names()
    if model.names:
        log.info("모델 클래스: %s", ", ".join(model.names))
    else:
        log.info("모델에 클래스 이름이 없습니다 - CLASS_MAP 의 키를 인덱스(0, 1, ...)로 쓰세요")
    return model

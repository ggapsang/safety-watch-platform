"""이미지 판별률 평가 — 카이스트 테스트베드 검증(시험 항목 5).

시험이 요구하는 것은 셋이다. 이 파일이 그 셋을 차례로 한다.

  1. 데이터를 불러온다          → `load_set()`      이미지 + 정답 라벨(YOLO txt)
  2. 학습된 모델로 분류한다      → `Detector`        객체감지 플러그인과 같은 모델·표·임계값
  3. 정답과 비교해 판별률을 낸다  → `match()`, `evaluate()`

  이미지 판별률 = 일치한 이미지 수 / 데이터 수 × 100   (기준 95 % 이상)

**판독은 플러그인과 똑같이 한다.** 모델 파일, 클래스 표(끈 클래스·표시 이름), 임계값을
객체감지 플러그인의 설정에서 그대로 읽는다. 시험 결과가 실제 관제 화면과 다르면 시험의
의미가 없다 — 그래서 판독 코드도 따로 쓰지 않고 플러그인의 inference 모듈을 그대로 쓴다.

**'일치' 는 객체 단위로 본다.** 한 이미지 안의 정답 객체가 모두 같은 클래스로 IoU 0.5 이상
겹치게 잡히고, 정답에 없는 탐지(헛탐지)가 하나도 없어야 그 이미지는 일치다. 정답 객체가
없는 이미지는 탐지도 없어야 일치다. 클래스만 맞으면 되는 느슨한 기준보다 엄격하지만,
'무엇을 어디서 잡았나' 를 그대로 따지므로 근거가 분명하다.

라벨 이름은 AMR / Human 이다(시험 항목 6번의 표기와 같다). 모델 클래스(AMR 모델의 "AMR",
COCO 모델의 "person")를 여기로 옮긴다 — `DEFAULT_MAP`.

실행
  docker compose exec mod-yolo python evaluate.py /eval/kaist
  docker compose exec mod-yolo python evaluate.py /eval/kaist --predict-only   정답 없이 판독만

데이터 배치(정답 라벨링 툴이 내보내는 모양 그대로 받는다)
  /eval/kaist/images/*.jpg          이미지 (또는 세트 폴더 바로 아래, 또는 obj_train_data/)
  /eval/kaist/labels/*.txt          이미지와 같은 이름. 줄마다 'cls cx cy w h' (0~1)
  /eval/kaist/classes.txt           클래스 이름(한 줄에 하나). obj.names 도 받는다

결과 — /eval/kaist/results/<YYYYmmdd-HHMMSS>/
  annotated/*.jpg   박스·라벨을 그린 이미지      predictions/*.txt  판독 결과(YOLO 형식+신뢰도)
  results.csv       이미지별 판정(엑셀용)        summary.json       판별률·클래스별 집계·실행 조건
"""

from __future__ import annotations

import csv
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, "/app")                     # 컨테이너 안: 플러그인 모듈과 _sdk 가 옆에 놓인다

# 평가에서 쓰는 클래스. 정답과 판독이 모두 이 이름으로 맞춰진 뒤 비교된다.
CLASSES = ("AMR", "Human")

# 모델 클래스(원래 key) -> 평가 클래스. 대소문자는 가리지 않는다.
DEFAULT_MAP = {"amr": "AMR", "person": "Human"}

# 정답 파일의 클래스 이름 -> 평가 클래스. 라벨링한 사람마다 이름을 다르게 붙이므로 흡수한다.
GT_ALIASES = {
    "amr": "AMR", "robot": "AMR", "agv": "AMR", "로봇": "AMR",
    "물류로봇": "AMR", "물류 로봇": "AMR",
    "human": "Human", "person": "Human", "people": "Human",
    "pedestrian": "Human", "사람": "Human",
}

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp")
MATCH_IOU = 0.5
CRITERION = 95.0

# results.csv 의 열 순서. 첫 줄은 사람이 읽을 한글 제목이라, 플러그인 화면은 이 순서로 읽는다.
CSV_COLUMNS = ["image", "verdict", *[f"gt_{c}" for c in CLASSES], *[f"det_{c}" for c in CLASSES],
               "matched", "missed", "false_pos", "detections"]


class EvalError(Exception):
    """사람이 고쳐야 하는 입력 문제. 메시지를 그대로 보여 준다."""


# ────────────────────────────────────────────────────────────── 1. 데이터 불러오기

@dataclass
class Box:
    cls: str                  # 평가 클래스 (AMR / Human)
    x1: float                 # 0~1 정규화
    y1: float
    x2: float
    y2: float
    score: float | None = None   # 정답 박스는 None


@dataclass
class Sample:
    image: Path
    label: Path | None        # 정답 파일. 판독 전용이면 None
    truth: list[Box] = field(default_factory=list)


def find_images(set_dir: Path) -> tuple[Path, list[Path]]:
    """이미지가 있는 폴더와 이미지 목록.

    images/ → obj_train_data/(CVAT 내보내기) → 세트 폴더 바로 아래 순서로 찾는다. 하위
    폴더를 통째로 뒤지지 않는다 — results/ 아래의 결과 이미지까지 데이터로 읽게 된다.
    """
    for d in (set_dir / "images", set_dir / "obj_train_data", set_dir):
        if d.is_dir():
            imgs = sorted(p for p in d.iterdir()
                          if p.is_file() and p.suffix.lower() in IMAGE_EXT)
            if imgs:
                return d, imgs
    return set_dir, []


def find_label(image: Path, set_dir: Path) -> Path | None:
    """이미지와 같은 이름의 정답 파일. labels/ 를 먼저 보고, 없으면 이미지 옆을 본다."""
    for d in (set_dir / "labels", image.parent):
        p = d / (image.stem + ".txt")
        if p.is_file():
            return p
    return None


def load_class_names(set_dir: Path, image_dir: Path) -> tuple[list[str], bool]:
    """정답 파일의 클래스 번호 -> 평가 클래스. (이름들, 가정했는가)

    classes.txt(LabelImg) 나 obj.names(CVAT) 가 있으면 그것을 읽는다. 없으면 0=AMR,
    1=Human 으로 가정한다 — 가정했다는 사실은 결과에 남겨 사람이 확인하게 한다.
    """
    for d in (set_dir, set_dir / "labels", image_dir):
        for name in ("classes.txt", "obj.names"):
            p = d / name
            if not p.is_file():
                continue
            names, unknown = [], []
            for raw in p.read_text(encoding="utf-8-sig").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                hit = GT_ALIASES.get(raw.lower()) or GT_ALIASES.get(raw)
                if hit is None:
                    unknown.append(raw)
                names.append(hit or raw)
            if unknown:
                raise EvalError(f"{p.name} 에 알 수 없는 클래스가 있습니다: {', '.join(unknown)} "
                                f"— AMR 또는 Human(사람·person 등)이어야 합니다")
            return names, False
    return list(CLASSES), True


def parse_yolo(path: Path, names: list[str]) -> list[Box]:
    """YOLO 라벨 한 파일. 줄마다 'cls cx cy w h' (0~1 정규화). 빈 줄·# 주석은 건너뛴다."""
    boxes = []
    for no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split()
        try:
            idx = int(float(parts[0]))
            cx, cy, w, h = (float(v) for v in parts[1:5])
        except (ValueError, IndexError):
            raise EvalError(f"{path.name}:{no} 줄을 읽지 못했습니다 — 'cls cx cy w h' 여야 "
                            f"합니다: {raw!r}") from None
        if not 0 <= idx < len(names):
            raise EvalError(f"{path.name}:{no} 클래스 번호 {idx} 가 클래스 목록"
                            f"({', '.join(names)}) 밖입니다")
        boxes.append(Box(names[idx], _clip(cx - w / 2), _clip(cy - h / 2),
                         _clip(cx + w / 2), _clip(cy + h / 2)))
    return boxes


def load_set(set_dir: Path, *, predict_only: bool = False) -> tuple[list[Sample], dict]:
    """세트 하나를 읽는다. (표본들, 정보)

    정답 파일이 하나도 없으면 판독 전용으로 내려간다. **일부만 없으면 멈춘다** — 시험에서
    정답 없는 이미지를 조용히 빼면 분모가 줄어 판별률이 부풀려진다.
    """
    if not set_dir.is_dir():
        raise EvalError(f"세트 폴더가 없습니다: {set_dir}")
    image_dir, images = find_images(set_dir)
    if not images:
        raise EvalError(f"이미지가 없습니다: {set_dir} (images/ 아래에 jpg·png 를 두세요)")

    labels = {img: (None if predict_only else find_label(img, set_dir)) for img in images}
    have = [img for img, lab in labels.items() if lab]
    mode = "evaluate" if have else "predict-only"
    if have and len(have) != len(images):
        missing = [img.name for img, lab in labels.items() if lab is None]
        raise EvalError(f"정답 파일이 없는 이미지가 {len(missing)}장 있습니다 — 전부 라벨링하거나 "
                        f"판독만(--predict-only) 하세요: " + ", ".join(missing[:10])
                        + (" …" if len(missing) > 10 else ""))

    names, assumed = (load_class_names(set_dir, image_dir) if mode == "evaluate"
                      else (list(CLASSES), False))
    samples = [Sample(img, lab, parse_yolo(lab, names) if lab else []) for img, lab in labels.items()]
    return samples, {"mode": mode, "image_dir": str(image_dir), "class_names": names,
                     "class_names_assumed": assumed}


# ────────────────────────────────────────────────────────────── 2. 모델로 분류하기

class Detector:
    """객체감지 플러그인과 같은 판독기.

    모델·클래스 표·끈 클래스·임계값을 플러그인 설정(cfg)에서 그대로 가져온다. 실시간
    워커(main.YoloSource)가 하는 것과 같은 순서로 거른다 — 끈 클래스를 빼고, 너무 작은
    박스를 빼고, 남은 것을 평가 클래스로 옮긴다.
    """

    def __init__(self, cfg, models: list[Path] | None = None,
                 class_map: dict[str, str] | None = None) -> None:
        import inference

        self.cfg = cfg
        self.paths = list(models) if models else list(cfg.common_models)
        if not self.paths:
            raise EvalError("판독할 모델이 없습니다 — 객체감지 플러그인에서 공통 모델을 켜거나 "
                            "--models 로 지정하세요")
        from main import _short_side_px             # 실시간 워커와 같은 크기 거르기

        self._short_side_px = _short_side_px
        self.map = {k.lower(): v for k, v in (class_map or DEFAULT_MAP).items()}
        self.engines = []
        for path in self.paths:
            if not path.is_file():
                raise EvalError(f"모델 파일이 없습니다: {path}")
            item_map, aliases, disabled = cfg.maps_for(path.name)
            self.engines.append((inference.load(path, cfg.device, cfg.imgsz, cfg.conf_thres,
                                                cfg.iou_thres, item_map, cfg.layout),
                                 aliases, disabled))
        self.ignored = 0          # 평가 클래스로 옮길 수 없어 버린 탐지 수

    @property
    def device(self) -> str:
        return self.engines[0][0].device if self.engines else "-"

    def detect(self, frame) -> list[Box]:
        h, w = frame.shape[:2]
        out = []
        for model, aliases, disabled in self.engines:
            for det in model.infer(frame):
                if disabled and det.label in disabled:
                    continue                      # 플러그인에서 끈 클래스 (COCO 의 의자·TV 등)
                if (self.cfg.min_box_px
                        and self._short_side_px(det, w, h) < self.cfg.min_box_px):
                    continue
                cls = (self.map.get(det.label.lower())
                       or self.map.get(aliases.get(det.label, "").lower()))
                if cls is None:
                    self.ignored += 1
                    continue
                out.append(Box(cls, det.x1, det.y1, det.x2, det.y2, round(det.score, 4)))
        return out

    def close(self) -> None:
        """ONNX 세션을 놓는다. 화면에서 여러 번 돌려도 GPU 메모리가 쌓이지 않게."""
        import gc

        self.engines.clear()
        gc.collect()


# ────────────────────────────────────────────────────────────── 3. 정답과 비교하기

def iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
    iy = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
    inter = ix * iy
    union = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Verdict:
    matched: list[tuple[int, int, float]]    # (정답 번호, 탐지 번호, IoU)
    missed: list[int]                        # 놓친 정답 번호
    false_pos: list[int]                     # 헛탐지 번호

    @property
    def ok(self) -> bool:
        return not self.missed and not self.false_pos


def match(truth: list[Box], pred: list[Box], thr: float = MATCH_IOU) -> Verdict:
    """정답과 탐지를 1:1 로 짝짓는다. 같은 클래스끼리, IoU 큰 순서로 먼저 가져간다.

    탐욕 매칭이다. IoU 가 같으면 번호 순으로 정해 매번 같은 결과가 나오게 한다 —
    시험에서 같은 데이터로 두 번 돌렸는데 숫자가 다르면 안 된다.
    """
    pairs = sorted(((iou(t, p), ti, pi)
                    for ti, t in enumerate(truth) for pi, p in enumerate(pred)
                    if t.cls == p.cls),
                   key=lambda x: (-x[0], x[1], x[2]))
    used_t, used_p, matched = set(), set(), []
    for v, ti, pi in pairs:
        if v < thr:
            break
        if ti in used_t or pi in used_p:
            continue
        used_t.add(ti)
        used_p.add(pi)
        matched.append((ti, pi, round(v, 4)))
    return Verdict(sorted(matched), [i for i in range(len(truth)) if i not in used_t],
                   [i for i in range(len(pred)) if i not in used_p])


# ────────────────────────────────────────────────────────────── 그리기

# 대시보드와 같은 색. web/src/lib/boxcolor.ts 의 팔레트와 해시를 그대로 옮겼다 —
# 결과 이미지와 관제 화면에서 AMR·Human 이 같은 색이어야 보는 사람이 헷갈리지 않는다.
PALETTE = ["#e8703a", "#4a9eda", "#5db872", "#d4a017",
           "#a978d8", "#3fb8ae", "#e0679b", "#8a9a5b"]
RED, GREEN = (40, 40, 230), (70, 180, 70)                  # BGR


def _djb2(text: str) -> int:
    h = 5381
    for unit in text.encode("utf-16-le")[::2]:             # JS charCodeAt 과 같은 단위
        h = ((h << 5) + h + unit) & 0xFFFFFFFF
    if h >= 2 ** 31:                                       # JS 의 '| 0' (부호 있는 32비트)
        h -= 2 ** 32
    return abs(h)


def box_color(label: str, overrides: dict[str, str] | None = None) -> tuple[int, int, int]:
    """라벨 -> 선 색(BGR). 관리자에서 덮어쓴 색이 있으면 그것, 없으면 이름 해시로 팔레트."""
    overrides = overrides or {}
    key = (label or "").split("#")[0].strip()
    hexcol = overrides.get(key) or overrides.get(label)
    if not hexcol:
        hexcol = PALETTE[_djb2(key) % len(PALETTE)] if key else PALETTE[0]
    r, g, b = (int(hexcol.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return b, g, r


def _dashed_rect(img, p1, p2, color, thickness, dash):
    import cv2

    (x1, y1), (x2, y2) = p1, p2
    for (ax, ay, bx, by) in ((x1, y1, x2, y1), (x2, y1, x2, y2), (x2, y2, x1, y2), (x1, y2, x1, y1)):
        length = max(abs(bx - ax), abs(by - ay))
        for s in range(0, length, dash * 2):
            t0, t1 = s / length, min(s + dash, length) / length
            cv2.line(img, (int(ax + (bx - ax) * t0), int(ay + (by - ay) * t0)),
                     (int(ax + (bx - ax) * t1), int(ay + (by - ay) * t1)), color, thickness)


def _tag(img, text, x, y, color, scale, thickness):
    """박스 위에 글자. 배경을 칠해 영상에 묻히지 않게 한다. 화면 위로 나가면 박스 안으로 내린다."""
    import cv2

    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    top = y - th - base - 4
    if top < 0:
        top = y + 2
    cv2.rectangle(img, (x, top), (x + tw + 6, top + th + base + 4), color, -1)
    cv2.putText(img, text, (x + 3, top + th + 2), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (255, 255, 255), thickness, cv2.LINE_AA)


def annotate(frame, pred: list[Box], truth: list[Box], verdict: Verdict | None,
             overrides: dict[str, str] | None = None):
    """판독 결과를 그린 이미지.

    탐지 박스는 클래스 색(대시보드와 같음)에 'AMR 0.92'. 정답과 비교했다면 헛탐지는 빨간
    글자로 'FP', 놓친 정답은 빨간 점선에 'MISSED Human'. 테두리는 일치면 초록, 아니면 빨강.
    글자는 영문만 쓴다 — OpenCV 로 한글을 그리면 깨진다.
    """
    import cv2

    img = frame.copy()
    h, w = img.shape[:2]
    unit = max(1.0, min(h, w) / 720)
    th, scale, ft = max(2, round(2 * unit)), 0.6 * unit, max(1, round(1.4 * unit))

    def px(b: Box):
        return (int(b.x1 * w), int(b.y1 * h)), (int(b.x2 * w), int(b.y2 * h))

    fp = set(verdict.false_pos) if verdict else set()
    for i, b in enumerate(pred):
        p1, p2 = px(b)
        col = box_color(b.cls, overrides)
        cv2.rectangle(img, p1, p2, col, th)
        text = f"{b.cls} {b.score:.2f}" if b.score is not None else b.cls
        _tag(img, text + (" FP" if i in fp else ""), p1[0], p1[1], RED if i in fp else col,
             scale, ft)

    if verdict:
        for i in verdict.missed:
            p1, p2 = px(truth[i])
            _dashed_rect(img, p1, p2, RED, th, max(6, round(10 * unit)))
            _tag(img, f"MISSED {truth[i].cls}", p1[0], p2[1] + round(22 * unit), RED, scale, ft)
        frame_col = GREEN if verdict.ok else RED
        cv2.rectangle(img, (0, 0), (w - 1, h - 1), frame_col, max(4, round(6 * unit)))
        head = (f"{'OK' if verdict.ok else 'NG'}  GT {len(truth)}  DET {len(pred)}"
                + ("" if verdict.ok else f"  missed {len(verdict.missed)}  FP {len(verdict.false_pos)}"))
        _tag(img, head, round(10 * unit), round(40 * unit), frame_col, scale * 1.2, ft + 1)
    return img


# ────────────────────────────────────────────────────────────── 실행

def _read_image(path: Path):
    """한글 파일 이름도 읽는다(cv2.imread 는 플랫폼에 따라 비ASCII 경로를 못 읽는다)."""
    import cv2
    import numpy as np

    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if img is None:
        raise EvalError(f"이미지를 열지 못했습니다: {path.name}")
    return img


def _write_jpg(path: Path, img) -> None:
    import cv2

    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise EvalError(f"결과 이미지를 쓰지 못했습니다: {path.name}")
    path.write_bytes(buf.tobytes())


def _yolo_line(b: Box) -> str:
    cx, cy = (b.x1 + b.x2) / 2, (b.y1 + b.y2) / 2
    return (f"{CLASSES.index(b.cls)} {cx:.6f} {cy:.6f} {b.x2 - b.x1:.6f} {b.y2 - b.y1:.6f}"
            + (f" {b.score:.4f}" if b.score is not None else ""))


def _box_colors(cfg) -> dict[str, str]:
    """관리자 화면에서 덮어쓴 박스 색. 플랫폼이 안 보이면 자동 색으로 간다."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{cfg.platform_url.rstrip('/')}/api/settings",   # noqa: S310
                                    timeout=3) as r:
            return json.loads(r.read().decode("utf-8")).get("box_colors") or {}
    except Exception:                                                               # noqa: BLE001
        return {}


def evaluate(set_dir: Path, cfg, *, out_dir: Path | None = None,
             models: list[Path] | None = None, class_map: dict[str, str] | None = None,
             match_iou: float = MATCH_IOU, criterion: float = CRITERION,
             predict_only: bool = False,
             progress: Callable[[int, int, str, bool | None], None] | None = None) -> dict:
    """세트 하나를 평가하고 결과 폴더에 남긴다. 요약(summary.json 과 같은 것)을 돌려준다.

    스크립트와 플러그인 화면이 둘 다 이 함수를 부른다 — 입구가 달라도 숫자는 같아야 한다.
    """
    started = time.time()
    samples, info = load_set(set_dir, predict_only=predict_only)
    out = out_dir or set_dir / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    (out / "annotated").mkdir(parents=True, exist_ok=True)
    (out / "predictions").mkdir(parents=True, exist_ok=True)
    # 판독 결과 txt 의 클래스 번호. 정답 파일의 번호 순서와 다를 수 있어 함께 남긴다.
    (out / "predictions" / "classes.txt").write_text("\n".join(CLASSES) + "\n", encoding="utf-8")

    detector = Detector(cfg, models, class_map)
    overrides = _box_colors(cfg)
    evaluating = info["mode"] == "evaluate"
    per_class = {c: {"tp": 0, "fp": 0, "fn": 0} for c in CLASSES}
    rows, matched_images = [], 0
    try:
        for n, s in enumerate(samples, 1):
            frame = _read_image(s.image)
            pred = detector.detect(frame)
            verdict = match(s.truth, pred, match_iou) if evaluating else None
            if verdict:
                matched_images += verdict.ok
                for ti, pi, _ in verdict.matched:
                    per_class[s.truth[ti].cls]["tp"] += 1
                for ti in verdict.missed:
                    per_class[s.truth[ti].cls]["fn"] += 1
                for pi in verdict.false_pos:
                    per_class[pred[pi].cls]["fp"] += 1

            _write_jpg(out / "annotated" / (s.image.stem + ".jpg"),
                       annotate(frame, pred, s.truth, verdict, overrides))
            (out / "predictions" / (s.image.stem + ".txt")).write_text(
                "".join(_yolo_line(b) + "\n" for b in pred), encoding="utf-8")
            rows.append({
                "image": s.image.name,
                "verdict": ("일치" if verdict.ok else "불일치") if verdict else "-",
                **{f"gt_{c}": sum(b.cls == c for b in s.truth) for c in CLASSES},
                **{f"det_{c}": sum(b.cls == c for b in pred) for c in CLASSES},
                "matched": len(verdict.matched) if verdict else "",
                "missed": len(verdict.missed) if verdict else "",
                "false_pos": len(verdict.false_pos) if verdict else "",
                "detections": "; ".join(f"{b.cls} {b.score:.2f}" for b in pred),
            })
            if progress:
                progress(n, len(samples), s.image.name, verdict.ok if verdict else None)
        device = detector.device
    finally:
        detector.close()

    total = len(samples)
    rate = round(matched_images / total * 100, 1) if evaluating and total else None
    summary = {
        "set": set_dir.name, "run": out.name, "mode": info["mode"],
        "started": datetime.fromtimestamp(started).isoformat(timespec="seconds"),
        "seconds": round(time.time() - started, 1),
        "data": total,
        "matched": matched_images if evaluating else None,
        "rate": rate,
        "criterion": criterion,
        "pass": (rate >= criterion) if rate is not None else None,
        "per_class": per_class if evaluating else None,
        "ignored_detections": detector.ignored,
        "models": [p.name for p in detector.paths],
        "device": device,
        "thresholds": {"conf": cfg.conf_thres, "nms_iou": cfg.iou_thres,
                       "match_iou": match_iou, "min_box_px": cfg.min_box_px},
        "class_map": dict(class_map or DEFAULT_MAP),
        "class_names": info["class_names"],
        "class_names_assumed": info["class_names_assumed"],
    }

    with (out / "results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        head ={"image": "이미지", "verdict": "판정", "matched": "맞춤", "missed": "놓침",
                "false_pos": "헛탐지", "detections": "탐지 상세",
                **{f"gt_{c}": f"정답 {c}" for c in CLASSES}, **{f"det_{c}": f"탐지 {c}" for c in CLASSES}}
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writerow(head)
        w.writerows(rows)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    return summary


def rate_line(summary: dict) -> str:
    """화면에 출력하는 판별률 한 줄. 시험 절차의 식을 그대로 보인다."""
    if summary["rate"] is None:
        return f"판독만 했습니다 — 데이터 {summary['data']}장 (정답이 없어 판별률은 없습니다)"
    verdict = "적합" if summary["pass"] else "부적합"
    return (f"이미지 판별률 = 일치 {summary['matched']} / 데이터 {summary['data']} × 100 = "
            f"{summary['rate']:.1f} %   (기준 {summary['criterion']:g} % 이상 → {verdict})")


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="이미지 판별률 평가 (카이스트 테스트베드)")
    ap.add_argument("set_dir", help="세트 폴더 (예 /eval/kaist)")
    ap.add_argument("--models", help="쉼표로 구분한 모델 파일 이름. 비우면 플러그인의 공통 모델")
    ap.add_argument("--map", help="모델 클래스=평가 클래스 (예 AMR=AMR,person=Human)")
    ap.add_argument("--match-iou", type=float, default=MATCH_IOU, help="일치로 볼 IoU (기본 0.5)")
    ap.add_argument("--criterion", type=float, default=CRITERION, help="적합 기준 % (기본 95)")
    ap.add_argument("--predict-only", action="store_true", help="정답과 비교하지 않고 판독만")
    args = ap.parse_args(argv)

    import logging

    import config as config_module

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    cfg = config_module.load()
    models = ([Path(cfg.models_dir) / n.strip() for n in args.models.split(",") if n.strip()]
              if args.models else None)
    class_map = (dict(kv.split("=", 1) for kv in args.map.split(",") if "=" in kv)
                 if args.map else None)

    def show(n, total, name, ok):
        mark = "" if ok is None else ("  일치" if ok else "  불일치")
        print(f"  [{n:3d}/{total}] {name}{mark}", flush=True)

    print(f"평가 세트: {args.set_dir}")
    try:
        summary = evaluate(Path(args.set_dir), cfg, models=models, class_map=class_map,
                           match_iou=args.match_iou, criterion=args.criterion,
                           predict_only=args.predict_only, progress=show)
    except EvalError as exc:
        print(f"\n평가를 멈췄습니다: {exc}", file=sys.stderr)
        return 2

    print()
    print(f"모델   {', '.join(summary['models'])} ({summary['device']})")
    print(f"임계값 conf {summary['thresholds']['conf']} · 일치 IoU {summary['thresholds']['match_iou']}")
    if summary["class_names_assumed"]:
        print("주의   classes.txt 가 없어 정답 클래스를 0=AMR, 1=Human 으로 가정했습니다")
    if summary["per_class"]:
        for c, v in summary["per_class"].items():
            print(f"{c:<6} 맞춤 {v['tp']} · 놓침 {v['fn']} · 헛탐지 {v['fp']}")
    print(f"결과   {Path(args.set_dir) / 'results' / summary['run']}")
    print()
    print("=" * 72)
    print(rate_line(summary))
    print("=" * 72)
    return 0


def _clip(v: float) -> float:
    return min(1.0, max(0.0, v))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

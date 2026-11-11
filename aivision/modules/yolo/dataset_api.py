"""데이터셋·라벨링 — 이미지를 보고 박스를 쳐서 YOLO 라벨로 쌓는다.

화면은 web/label.html. 여기는 그 뒤의 API 다.

데이터 자리는 `/datasets`(호스트의 DATASET_PATH)다. 데이터셋 하나가 폴더 하나 —
  /datasets/<이름>/images/*.jpg      이미지
  /datasets/<이름>/labels/*.txt      이미지와 같은 이름. 줄마다 'cls cx cy w h' (0~1)
  /datasets/<이름>/classes.txt       클래스 이름(한 줄에 하나, 줄 번호가 cls)
  /datasets/<이름>/review.json       사람이 본 결과 — {이미지: {"status": "ok"|"skip", "at": …}}
  /datasets/<이름>/.thumbs/          목록 썸네일(지워도 다시 만든다)
  /datasets/<이름>/.trash/           지운 이미지·라벨(되살릴 수 있게 옮겨만 둔다)

**형식을 따로 만들지 않는다.** Roboflow·CVAT·LabelImg 가 내보내는 YOLO 모양 그대로라,
밖에서 받은 폴더를 그냥 두면 열리고, 여기서 만든 것을 그대로 학습·평가에 쓴다.

라벨 파일이 '없는' 것과 '비어 있는' 것은 다르다. 없으면 아직 아무도 안 본 이미지고,
비어 있으면 사람이 보고 '여기엔 아무것도 없다' 고 정한 이미지다. 저장은 늘 파일을 만든다.

모델로 초안 만들기(자동 라벨)는 평가와 같은 판독기(evaluate.Detector)를 쓴다 — 실시간
화면과 같은 모델·클래스 표·임계값이다. 세션은 잠깐 쓰고 놓는다(GPU 절약, `_Drafter`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

import evaluate

log = logging.getLogger(__name__)

DATASET_DIR = Path(os.environ.get("DATASET_DIR", "/datasets"))
EVAL_DIR = Path(os.environ.get("EVAL_DIR", "/eval"))
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
IMAGE_EXT = evaluate.IMAGE_EXT
THUMB_W = (160, 320, 640)
MAX_UPLOAD_FILES = 5000
MAX_UPLOAD_MB = 4096
STATUSES = ("ok", "skip")
# 자동 라벨 판독기를 이만큼 안 쓰면 놓는다. 한 장씩 넘기며 누를 때마다 세션을 새로 열면
# 몇 초씩 걸리고, 계속 들고 있으면 GPU 를 그만큼 잡아 둔다.
DRAFTER_IDLE_SEC = 120


class BoxIn(BaseModel):
    cls: int = Field(ge=0, le=999)
    x1: float
    y1: float
    x2: float
    y2: float


class LabelIn(BaseModel):
    boxes: list[BoxIn]
    status: str | None = None          # "ok" | "skip" | "" (검토 표시 지움) | None (그대로)


class ReviewIn(BaseModel):
    status: str                        # "ok" | "skip" | ""


class ClassesIn(BaseModel):
    classes: list[str]


class CreateIn(BaseModel):
    name: str
    classes: list[str] = ["AMR", "Human"]


class CaptureIn(BaseModel):
    camera_id: int


class DraftIn(BaseModel):
    min_score: float = Field(default=0.5, ge=0.0, le=1.0)


class BulkDraftIn(BaseModel):
    min_score: float = Field(default=0.5, ge=0.0, le=1.0)
    only_unlabeled: bool = True


class ToEvalIn(BaseModel):
    name: str
    status: str = "ok"                  # "ok" 만 / "all" 전부(제외 표시는 늘 뺀다)


# ────────────────────────────────────────────────────────────── 폴더 읽고 쓰기

# **파일마다 stat 하지 않는다.** 데이터셋은 윈도 호스트 폴더를 붙인 것이라 stat 한 번이
# 2ms 쯤 든다. evaluate.find_images 처럼 파일마다 is_file() 을 부르면 2,238장짜리 세트는
# 이미지 한 장을 열 때마다 5초, 목록은 24초가 걸렸다. 폴더 목록(listdir)은 한 번에 온다.

def _names(d: Path) -> list[str]:
    try:
        return os.listdir(d)
    except OSError:
        return []


def _is_image(name: str) -> bool:
    return name.lower().endswith(IMAGE_EXT) and not name.startswith(".")


def image_dir(ds: Path) -> Path:
    """이미지 폴더. 밖에서 받은 모양(obj_train_data/, 폴더 바로 아래)도 연다 —
    evaluate.find_images 와 같은 순서."""
    for d in (ds / "images", ds / "obj_train_data", ds):
        if any(_is_image(n) for n in _names(d)):
            return d
    return ds / "images"


def list_images(ds: Path) -> list[Path]:
    d = image_dir(ds)
    return [d / n for n in sorted(n for n in _names(d) if _is_image(n))]


def label_files(ds: Path) -> dict[str, Path]:
    """이미지 이름(확장자 뺀 것) → 라벨 파일. evaluate.find_label 과 같은 순서(labels/ 먼저)."""
    out: dict[str, Path] = {}
    for d in (ds / "labels", image_dir(ds)):
        for n in _names(d):
            if n.lower().endswith(".txt") and n.lower() not in ("classes.txt",) and not n.startswith("."):
                out.setdefault(n[:-4], d / n)
    return out


def count_boxes(path: Path) -> int:
    with path.open("rb") as f:
        return sum(1 for ln in f.read().decode("utf-8-sig", "replace").splitlines()
                   if len(ln.split()) >= 5 and not ln.lstrip().startswith("#"))


def label_path(ds: Path, image_name: str) -> Path:
    """쓸 때의 라벨 자리. 읽을 때는 `find_label` 이 옆자리도 본다."""
    return ds / "labels" / (Path(image_name).stem + ".txt")


def read_classes(ds: Path) -> list[str]:
    for d in (ds, ds / "labels"):
        for name in ("classes.txt", "obj.names"):
            p = d / name
            if p.is_file():
                return [ln.strip() for ln in p.read_text(encoding="utf-8-sig").splitlines()
                        if ln.strip()]
    return []


def write_text(path: Path, text: str) -> None:
    """반쯤 쓴 파일이 남지 않게 옆에 쓰고 바꿔 넣는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_boxes(path: Path) -> list[dict]:
    """YOLO txt → [{cls, x1, y1, x2, y2}] (0~1). 깨진 줄은 건너뛴다 — 고칠 수 있게 연다."""
    out = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        parts = raw.split()
        if len(parts) < 5 or raw.lstrip().startswith("#"):
            continue
        try:
            cls = int(float(parts[0]))
            cx, cy, w, h = (float(v) for v in parts[1:5])
        except ValueError:
            continue
        out.append({"cls": cls, "x1": _clip(cx - w / 2), "y1": _clip(cy - h / 2),
                    "x2": _clip(cx + w / 2), "y2": _clip(cy + h / 2)})
    return out


def yolo_text(boxes: list[BoxIn | dict]) -> str:
    lines = []
    for b in boxes:
        b = b if isinstance(b, dict) else b.model_dump()
        x1, x2 = sorted((_clip(b["x1"]), _clip(b["x2"])))
        y1, y2 = sorted((_clip(b["y1"]), _clip(b["y2"])))
        if x2 - x1 < 1e-4 or y2 - y1 < 1e-4:
            continue                                   # 클릭 한 번으로 생긴 점 같은 박스
        lines.append(f"{int(b['cls'])} {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} "
                     f"{x2 - x1:.6f} {y2 - y1:.6f}")
    return "".join(ln + "\n" for ln in lines)


def box_iou(a: dict, b: dict) -> float:
    w = max(0.0, min(a["x2"], b["x2"]) - max(a["x1"], b["x1"]))
    h = max(0.0, min(a["y2"], b["y2"]) - max(a["y1"], b["y1"]))
    inter = w * h
    union = ((a["x2"] - a["x1"]) * (a["y2"] - a["y1"])
             + (b["x2"] - b["x1"]) * (b["y2"] - b["y1"]) - inter)
    return inter / union if union > 0 else 0.0


def read_review(ds: Path) -> dict:
    try:
        data = json.loads((ds / "review.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _clip(v: float) -> float:
    return min(1.0, max(0.0, float(v)))


# ────────────────────────────────────────────────────────────── 자동 라벨 판독기

class _Drafter:
    """evaluate.Detector 를 잠깐 들고 있다가 놓는다.

    모델·임계값이 바뀌면(플러그인에서 공통 모델을 바꾸면) 다음 요청에서 새로 연다.
    """

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.lock = threading.Lock()
        self.det = None
        self.key = None
        self.used = 0.0
        threading.Thread(target=self._reaper, name="drafter-reaper", daemon=True).start()

    def _key(self, class_map):
        c = self.cfg
        return (tuple(str(p) for p in c.common_models), c.conf_thres, c.iou_thres,
                c.min_box_px, tuple(sorted(class_map.items())))

    def detect(self, frame, class_map: dict[str, str]):
        with self.lock:
            key = self._key(class_map)
            if self.det is None or self.key != key:
                if self.det is not None:
                    self.det.close()
                self.det = None
                self.det = evaluate.Detector(self.cfg, class_map=class_map)
                self.key = key
                log.info("자동 라벨 판독기 열림: %s", ", ".join(p.name for p in self.det.paths))
            self.used = time.monotonic()
            return self.det.detect(frame)

    def _reaper(self) -> None:
        while True:
            time.sleep(15)
            with self.lock:
                if self.det is not None and time.monotonic() - self.used > DRAFTER_IDLE_SEC:
                    self.det.close()
                    self.det = None
                    self.key = None
                    log.info("자동 라벨 판독기 닫음 (%d초 안 씀)", DRAFTER_IDLE_SEC)


def class_map_for(classes: list[str]) -> tuple[dict[str, str], dict[str, int]]:
    """데이터셋 클래스에 맞춘 (모델 클래스 → 이름, 이름 → 데이터셋 번호).

    모델 쪽 이름(person, AMR …)을 평가 어휘(GT_ALIASES: 사람·person → Human)로 흡수해
    데이터셋 클래스와 잇는다. 데이터셋에 없는 클래스는 초안에 안 넣는다.
    """
    index: dict[str, int] = {}
    for i, c in enumerate(classes):
        index.setdefault(c, i)
        canon = evaluate.GT_ALIASES.get(c.lower()) or evaluate.GT_ALIASES.get(c)
        if canon:
            index.setdefault(canon, i)
    cmap = dict(evaluate.DEFAULT_MAP)
    for c in classes:
        cmap.setdefault(c.lower(), c)
    for alias, canon in evaluate.GT_ALIASES.items():
        cmap.setdefault(alias.lower(), canon)
    return cmap, index


# ────────────────────────────────────────────────────────────── API

def mount(app: FastAPI, cfg) -> None:
    drafter = _Drafter(cfg)
    bulk: dict = {}                       # 일괄 자동 라벨 진행(한 번에 하나)
    bulk_lock = threading.Lock()
    # 라벨 파일 경로 → 박스 수. 목록을 열 때마다 수천 개를 다시 읽지 않게. 이 화면이 쓰는
    # 라벨은 쓸 때 같이 고친다(`_write_label`). 탐색기에서 바꾼 파일은 컨테이너를 다시
    # 띄울 때까지 옛 수가 보일 수 있다 — 목록의 숫자일 뿐, 편집기는 늘 파일을 새로 읽는다.
    counts: dict[str, int] = {}

    def _write_label(lab: Path, boxes) -> int:
        text = yolo_text(boxes)
        write_text(lab, text)
        counts[str(lab)] = text.count("\n")
        return counts[str(lab)]

    def _root() -> Path:
        if not DATASET_DIR.is_dir():
            raise HTTPException(status_code=503,
                                detail=f"데이터셋 폴더({DATASET_DIR})가 붙어 있지 않습니다 — "
                                       ".env 의 DATASET_PATH 를 확인하세요")
        return DATASET_DIR

    def _ds(name: str) -> Path:
        if not NAME.match(name):
            raise HTTPException(status_code=400, detail="데이터셋 이름이 올바르지 않습니다")
        d = _root() / name
        if not d.is_dir():
            raise HTTPException(status_code=404, detail=f"데이터셋이 없습니다: {name}")
        return d

    def _image(ds: Path, name: str) -> Path:
        p = image_dir(ds) / name
        if (Path(name).name != name or name.startswith(".")
                or p.suffix.lower() not in IMAGE_EXT or not p.is_file()):
            raise HTTPException(status_code=404, detail=f"이미지가 없습니다: {name}")
        return p

    def _set_review(ds: Path, names: list[str], status: str) -> None:
        if status not in ("", *STATUSES):
            raise HTTPException(status_code=400, detail="검토 표시는 ok·skip·'' 중 하나입니다")
        review = read_review(ds)
        for n in names:
            if status:
                review[n] = {"status": status, "at": datetime.now().isoformat(timespec="seconds")}
            else:
                review.pop(n, None)
        write_text(ds / "review.json", json.dumps(review, ensure_ascii=False, indent=0))

    def _summary(ds: Path) -> dict:
        review = read_review(ds)
        imgs = list_images(ds)
        names = {p.name for p in imgs}
        ok = sum(1 for n, r in review.items() if n in names and r.get("status") == "ok")
        skip = sum(1 for n, r in review.items() if n in names and r.get("status") == "skip")
        return {"name": ds.name, "images": len(imgs), "ok": ok, "skip": skip,
                "classes": read_classes(ds)}

    # ── 데이터셋 ────────────────────────────────────────────────────

    @app.get("/api/datasets")
    async def list_datasets() -> JSONResponse:
        if not DATASET_DIR.is_dir():
            return JSONResponse({"items": [], "error": f"데이터셋 폴더({DATASET_DIR})가 붙어 있지 "
                                                     "않습니다 — .env 의 DATASET_PATH 를 확인하세요"})

        def scan():
            return [_summary(d) for d in sorted(DATASET_DIR.iterdir(), key=lambda p: p.name.lower())
                    if d.is_dir() and NAME.match(d.name)]
        return JSONResponse({"items": await asyncio.to_thread(scan)})

    @app.post("/api/datasets")
    async def create_dataset(body: CreateIn) -> JSONResponse:
        name = body.name.strip()
        if not NAME.match(name):
            raise HTTPException(status_code=400,
                                detail="이름은 영문·숫자·. _ - 로 지어 주세요 (예 amr-human-cam3)")
        classes = [c.strip() for c in body.classes if c.strip()]
        if not classes or len(set(classes)) != len(classes):
            raise HTTPException(status_code=400, detail="클래스를 하나 이상, 겹치지 않게 적어 주세요")
        d = _root() / name
        if d.exists():
            raise HTTPException(status_code=409, detail=f"같은 이름이 이미 있습니다: {name}")
        (d / "images").mkdir(parents=True)
        (d / "labels").mkdir()
        write_text(d / "classes.txt", "".join(c + "\n" for c in classes))
        log.info("데이터셋 만듦: %s (%s)", name, ", ".join(classes))
        return JSONResponse(_summary(d), status_code=201)

    @app.get("/api/datasets/{name}")
    async def get_dataset(name: str) -> JSONResponse:
        """이미지 목록과 각 이미지의 박스 수·검토 표시. 목록 화면이 이것 하나로 그린다."""
        ds = _ds(name)

        def scan():
            review = read_review(ds)
            labels = label_files(ds)
            todo = [p for p in labels.values() if str(p) not in counts]
            if todo:
                # 처음 여는 세트만 라벨 파일을 다 읽는다. 여러 개를 겹쳐 읽으면 붙인 폴더의
                # 느린 파일 열기가 겹쳐져 2,238장이 10초 → 2초.
                def one(p: Path):
                    try:
                        return str(p), count_boxes(p)
                    except OSError:
                        return str(p), None
                with ThreadPoolExecutor(8) as ex:
                    for key, n in ex.map(one, todo):
                        if n is not None:
                            counts[key] = n
            items = []
            for img in list_images(ds):
                lab = labels.get(img.stem)
                r = review.get(img.name) or {}
                items.append({"name": img.name, "boxes": counts.get(str(lab)) if lab else None,
                              "status": r.get("status", "")})
            return items

        return JSONResponse({**_summary(ds), "items": await asyncio.to_thread(scan)})

    @app.put("/api/datasets/{name}/classes")
    async def set_classes(name: str, body: ClassesIn) -> JSONResponse:
        """클래스 이름을 고치거나 뒤에 더한다. **지우거나 순서를 바꾸지는 않는다** —
        라벨 파일은 번호로 적혀 있어, 줄이 밀리면 모든 박스의 클래스가 조용히 바뀐다."""
        ds = _ds(name)
        old = read_classes(ds)
        new = [c.strip() for c in body.classes]
        if len(new) < len(old) or any(not c for c in new) or len(set(new)) != len(new):
            raise HTTPException(status_code=400,
                                detail="클래스는 이름을 바꾸거나 뒤에 더하기만 됩니다(빈 이름·겹침 안 됨)")
        write_text(ds / "classes.txt", "".join(c + "\n" for c in new))
        return JSONResponse({"classes": new})

    # ── 이미지 ──────────────────────────────────────────────────────

    @app.get("/api/datasets/{name}/image/{image}")
    async def get_image(name: str, image: str, w: int = 0) -> Response:
        """원본, 또는 `w`(160·320·640) 폭 썸네일. 썸네일은 .thumbs/ 에 만들어 두고 다시 쓴다."""
        ds = _ds(name)
        path = _image(ds, image)
        if not w:
            return FileResponse(str(path), headers={"Cache-Control": "no-cache"})
        w = min(THUMB_W, key=lambda t: abs(t - w))
        thumb = ds / ".thumbs" / str(w) / (path.stem + ".jpg")

        def make() -> bytes:
            if thumb.is_file() and thumb.stat().st_mtime >= path.stat().st_mtime:
                return thumb.read_bytes()
            import cv2

            img = evaluate._read_image(path)
            if img.shape[1] > w:
                img = cv2.resize(img, (w, round(img.shape[0] * w / img.shape[1])),
                                 interpolation=cv2.INTER_AREA)
            data = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 78])[1].tobytes()
            try:
                thumb.parent.mkdir(parents=True, exist_ok=True)
                thumb.write_bytes(data)
            except OSError:
                pass                                   # 읽기 전용 폴더여도 보여는 준다
            return data

        return Response(await asyncio.to_thread(make), media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=3600"})

    @app.post("/api/datasets/{name}/upload")
    async def upload(name: str, request: Request) -> JSONResponse:
        """이미지(와 같은 이름의 YOLO txt)를 더한다. 같은 이름의 이미지가 있으면 건너뛴다 —
        덮어쓰면 그 이미지에 쳐 둔 박스가 엉뚱한 사진에 붙는다."""
        ds = _ds(name)
        form = await request.form(max_files=MAX_UPLOAD_FILES, max_fields=MAX_UPLOAD_FILES + 10)
        files = form.getlist("files")
        if not files:
            raise HTTPException(status_code=400, detail="올린 파일이 없습니다")
        imgdir = image_dir(ds)
        imgdir.mkdir(parents=True, exist_ok=True)
        existing = {p.name.lower() for p in list_images(ds)}
        added, dup, skipped, size = [], [], 0, 0
        labels: dict[str, bytes] = {}
        for f in files:
            fname = Path(str(f.filename or "").replace("\\", "/")).name
            ext = Path(fname).suffix.lower()
            if not fname or fname.startswith("."):
                skipped += 1
                continue
            data = await f.read()
            size += len(data)
            if size > MAX_UPLOAD_MB * 1024 * 1024:
                raise HTTPException(status_code=413, detail=f"너무 큽니다 (상한 {MAX_UPLOAD_MB}MB)")
            if ext == ".txt" and fname.lower() not in ("classes.txt",):
                labels[Path(fname).stem] = data
            elif ext in IMAGE_EXT:
                if fname.lower() in existing:
                    dup.append(fname)
                    continue
                (imgdir / fname).write_bytes(data)
                existing.add(fname.lower())
                added.append(fname)
            else:
                skipped += 1
        with_label = 0
        for fname in added:
            data = labels.get(Path(fname).stem)
            if data is not None:
                lp = label_path(ds, fname)
                lp.parent.mkdir(parents=True, exist_ok=True)
                lp.write_bytes(data)
                counts.pop(str(lp), None)
                with_label += 1
        log.info("데이터셋 %s: 이미지 %d장 더함(라벨 %d), 같은 이름 %d, 건너뜀 %d",
                 name, len(added), with_label, len(dup), skipped)
        return JSONResponse({"added": added, "with_label": with_label, "duplicates": dup,
                             "skipped": skipped}, status_code=201)

    @app.get("/api/datasets-cameras")
    async def cameras() -> JSONResponse:
        """카메라에서 담기용 목록. 플랫폼의 카메라 전체(이 모듈 담당이 아니어도 된다)."""
        def fetch():
            with urllib.request.urlopen(f"{cfg.platform_url.rstrip('/')}/api/cameras",   # noqa: S310
                                        timeout=5) as r:
                return json.loads(r.read().decode("utf-8"))
        try:
            rows = await asyncio.to_thread(fetch)
        except Exception as exc:                                                     # noqa: BLE001
            return JSONResponse({"items": [], "error": f"플랫폼에 연결하지 못했습니다: {exc}"})
        return JSONResponse({"items": [
            {"id": c.get("id"), "name": c.get("name") or "", "location": c.get("location") or "",
             "status": c.get("status") or ""}
            for c in rows if isinstance(c, dict) and c.get("enabled", True)]})

    @app.post("/api/datasets/{name}/capture")
    async def capture(name: str, body: CaptureIn) -> JSONResponse:
        """카메라의 지금 프레임 한 장(원본 해상도)을 데이터셋에 담는다."""
        ds = _ds(name)
        url = f"{cfg.platform_url.rstrip('/')}/api/stream/{int(body.camera_id)}/snapshot.jpg"

        def grab() -> bytes:
            with urllib.request.urlopen(url, timeout=10) as r:                       # noqa: S310
                return r.read()
        try:
            data = await asyncio.to_thread(grab)
        except Exception as exc:                                                     # noqa: BLE001
            raise HTTPException(status_code=502,
                                detail=f"카메라 {body.camera_id} 프레임을 받지 못했습니다: {exc}") from None
        imgdir = image_dir(ds)
        imgdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        fname = f"cam{body.camera_id}_{stamp}.jpg"
        n = 1
        while (imgdir / fname).exists():
            n += 1
            fname = f"cam{body.camera_id}_{stamp}-{n}.jpg"
        (imgdir / fname).write_bytes(data)
        return JSONResponse({"name": fname}, status_code=201)

    @app.delete("/api/datasets/{name}/image/{image}")
    async def delete_image(name: str, image: str) -> JSONResponse:
        """지우지 않고 .trash/ 로 옮긴다. 잘못 눌러도 탐색기에서 되살릴 수 있다."""
        ds = _ds(name)
        path = _image(ds, image)
        trash = ds / ".trash" / datetime.now().strftime("%Y%m%d")
        trash.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(trash / path.name))
        lab = evaluate.find_label(path, ds)
        if lab is not None and lab.is_file():
            shutil.move(str(lab), str(trash / lab.name))
            counts.pop(str(lab), None)
        _set_review(ds, [image], "")
        return JSONResponse({"trashed": image, "to": str(trash.relative_to(ds))})

    # ── 라벨 ────────────────────────────────────────────────────────

    @app.get("/api/datasets/{name}/label/{image}")
    async def get_label(name: str, image: str) -> JSONResponse:
        ds = _ds(name)
        path = _image(ds, image)
        lab = evaluate.find_label(path, ds)
        r = read_review(ds).get(image) or {}
        return JSONResponse({"name": image, "exists": lab is not None,
                             "boxes": read_boxes(lab) if lab else [],
                             "status": r.get("status", "")})

    @app.put("/api/datasets/{name}/label/{image}")
    async def put_label(name: str, image: str, body: LabelIn) -> JSONResponse:
        ds = _ds(name)
        path = _image(ds, image)
        ncls = len(read_classes(ds))
        bad = [b.cls for b in body.boxes if b.cls >= ncls]
        if bad:
            raise HTTPException(status_code=400,
                                detail=f"클래스 번호 {bad[0]} 이 클래스 목록({ncls}개) 밖입니다")
        # 이미지 옆에 라벨이 있던 세트(밖에서 받은 것)는 그 자리에 쓴다 — 두 곳에 두면
        # 어느 것이 맞는지 모른다.
        lab = evaluate.find_label(path, ds) or label_path(ds, image)
        _write_label(lab, body.boxes)
        if body.status is not None:
            _set_review(ds, [image], body.status)
        r = read_review(ds).get(image) or {}
        return JSONResponse({"name": image, "boxes": len(body.boxes), "status": r.get("status", "")})

    @app.put("/api/datasets/{name}/review/{image}")
    async def put_review(name: str, image: str, body: ReviewIn) -> JSONResponse:
        ds = _ds(name)
        _image(ds, image)
        _set_review(ds, [image], body.status)
        return JSONResponse({"name": image, "status": body.status})

    # ── 모델로 초안 ─────────────────────────────────────────────────

    @app.post("/api/datasets/{name}/draft/{image}")
    async def draft(name: str, image: str, body: DraftIn) -> JSONResponse:
        """켜 둔 공통 모델로 이 이미지를 판독해 박스 초안을 돌려준다. **저장하지 않는다** —
        화면에서 사람이 고친 뒤 저장한다."""
        ds = _ds(name)
        path = _image(ds, image)
        classes = read_classes(ds)
        cmap, index = class_map_for(classes)

        def run():
            frame = evaluate._read_image(path)
            return drafter.detect(frame, cmap)
        try:
            boxes = await asyncio.to_thread(run)
        except evaluate.EvalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        out = [{"cls": index[b.cls], "x1": b.x1, "y1": b.y1, "x2": b.x2, "y2": b.y2,
                "score": b.score}
               for b in boxes if b.cls in index and (b.score or 0) >= body.min_score]
        return JSONResponse({"boxes": out, "models": [p.name for p in drafter.det.paths]
                             if drafter.det else []})

    @app.post("/api/datasets/{name}/draft-all")
    async def draft_all(name: str, body: BulkDraftIn) -> JSONResponse:
        """라벨이 없는 이미지 전부(또는 검토 안 한 것 전부)에 초안을 써 둔다. 백그라운드."""
        ds = _ds(name)
        classes = read_classes(ds)
        cmap, index = class_map_for(classes)
        with bulk_lock:
            if bulk.get("status") == "running":
                raise HTTPException(status_code=409,
                                    detail=f"자동 라벨이 이미 돌고 있습니다: {bulk['dataset']} "
                                           f"({bulk['done']}/{bulk['total']})")
            review = read_review(ds)
            labels = label_files(ds)
            targets = [img for img in list_images(ds)
                       if (img.stem not in labels if body.only_unlabeled
                           else not (review.get(img.name) or {}).get("status"))]
            bulk.clear()
            bulk.update(dataset=name, status="running", done=0, total=len(targets),
                        boxes=0, error=None)

        def work():
            try:
                for img in targets:
                    boxes = drafter.detect(evaluate._read_image(img), cmap)
                    new = [{"cls": index[b.cls], "x1": b.x1, "y1": b.y1, "x2": b.x2, "y2": b.y2}
                           for b in boxes if b.cls in index and (b.score or 0) >= body.min_score]
                    lab = labels.get(img.stem)
                    # 있던 박스(사람이 친 정답일 수 있다)는 그대로 두고, 겹치지 않는 초안만 더한다.
                    old = read_boxes(lab) if lab else []
                    add = [n for n in new
                           if not any(o["cls"] == n["cls"] and box_iou(o, n) > 0.5 for o in old)]
                    _write_label(lab or label_path(ds, img.name), old + add)
                    bulk["done"] += 1
                    bulk["boxes"] += len(add)
                bulk["status"] = "done"
                log.info("자동 라벨 끝: %s %d장, 박스 %d", name, bulk["done"], bulk["boxes"])
            except Exception as exc:                                                 # noqa: BLE001
                bulk.update(status="error", error=str(exc))
                log.exception("자동 라벨 실패: %s", name)

        threading.Thread(target=work, name=f"draft-{name}", daemon=True).start()
        return JSONResponse(dict(bulk), status_code=202)

    @app.get("/api/datasets-job")
    async def bulk_status() -> JSONResponse:
        return JSONResponse(dict(bulk) or {"status": "idle"})

    # ── 내보내기 ────────────────────────────────────────────────────

    def _picked(ds: Path, status: str) -> list[Path]:
        review = read_review(ds)
        out = []
        for img in list_images(ds):
            s = (review.get(img.name) or {}).get("status", "")
            if s == "skip":
                continue
            if status == "ok" and s != "ok":
                continue
            out.append(img)
        return out

    @app.get("/api/datasets/{name}/export.zip")
    async def export_zip(name: str, status: str = "ok") -> FileResponse:
        """YOLO 모양 그대로 묶는다(images/·labels/·classes.txt). Roboflow·학습에 바로 넣는다."""
        ds = _ds(name)

        def build() -> str:
            imgs = _picked(ds, status)
            if not imgs:
                raise HTTPException(status_code=400, detail="내보낼 이미지가 없습니다 "
                                                            "(검토에서 '사용' 으로 표시한 것이 없음)")
            fd, tmp = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            labels = label_files(ds)
            with zipfile.ZipFile(tmp, "w") as z:
                z.writestr(f"{name}/classes.txt", "".join(c + "\n" for c in read_classes(ds)))
                for img in imgs:
                    z.write(img, f"{name}/images/{img.name}", compress_type=zipfile.ZIP_STORED)
                    lab = labels.get(img.stem)
                    z.writestr(f"{name}/labels/{img.stem}.txt",
                               lab.read_text(encoding="utf-8-sig") if lab else "",
                               compress_type=zipfile.ZIP_DEFLATED)
            return tmp

        tmp = await asyncio.to_thread(build)
        return FileResponse(tmp, media_type="application/zip", filename=f"{name}-{status}.zip",
                            background=BackgroundTask(os.unlink, tmp))

    @app.post("/api/datasets/{name}/to-eval")
    async def to_eval(name: str, body: ToEvalIn) -> JSONResponse:
        """고른 이미지로 평가 세트(/eval/<이름>)를 만든다. 아래 '이미지 판별률 평가' 에서 바로
        고를 수 있다. 라벨이 없는 이미지가 섞여 있으면 만들지 않는다 — 평가는 전부 정답이
        있어야 돈다(evaluate.load_set)."""
        ds = _ds(name)
        target = body.name.strip()
        if not NAME.match(target):
            raise HTTPException(status_code=400, detail="세트 이름은 영문·숫자·. _ - 로 지어 주세요")
        if not EVAL_DIR.is_dir():
            raise HTTPException(status_code=503, detail=f"평가 폴더({EVAL_DIR})가 붙어 있지 않습니다")
        dest = EVAL_DIR / target
        if dest.exists():
            raise HTTPException(status_code=409, detail=f"같은 이름의 평가 세트가 이미 있습니다: {target}")

        def build() -> dict:
            imgs = _picked(ds, body.status)
            if not imgs:
                raise HTTPException(status_code=400, detail="보낼 이미지가 없습니다 "
                                                            "(검토에서 '사용' 으로 표시한 것이 없음)")
            labels = label_files(ds)
            missing = [i.name for i in imgs if i.stem not in labels]
            if missing:
                raise HTTPException(status_code=400,
                                    detail=f"라벨이 없는 이미지가 {len(missing)}장 있습니다: "
                                           + ", ".join(missing[:5]) + (" …" if len(missing) > 5 else ""))
            tmp = Path(tempfile.mkdtemp(prefix=".from-dataset-", dir=str(EVAL_DIR)))
            try:
                (tmp / "images").mkdir()
                (tmp / "labels").mkdir()
                write_text(tmp / "classes.txt", "".join(c + "\n" for c in read_classes(ds)))
                for img in imgs:
                    shutil.copy2(img, tmp / "images" / img.name)
                    shutil.copy2(labels[img.stem], tmp / "labels" / (img.stem + ".txt"))
                evaluate.load_set(tmp)               # 평가가 못 읽는 세트면 여기서 멈춘다
                tmp.rename(dest)
            except evaluate.EvalError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            return {"set": target, "images": len(imgs)}

        result = await asyncio.to_thread(build)
        log.info("평가 세트 만듦: %s ← %s (%d장)", target, name, result["images"])
        return JSONResponse(result, status_code=201)

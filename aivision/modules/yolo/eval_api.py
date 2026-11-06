"""이미지 판별률 평가 — 플러그인 화면 입구.

스크립트(`python evaluate.py /eval/<세트>`)와 같은 `evaluate.evaluate()` 를 부른다. 입구가
둘이어도 판독 코어는 하나라, 같은 세트를 돌리면 같은 숫자가 나온다.

데이터 자리는 `/eval`(호스트의 EVAL_PATH)이다. 세트 하나가 폴더 하나 —
  /eval/<세트>/images/*.jpg, labels/*.txt, classes.txt   (evaluate.py 머리말 참조)
  /eval/<세트>/results/<YYYYmmdd-HHMMSS>/                 결과

**평가는 한 번에 하나만 돌린다.** 평가는 자기 ONNX 세션을 따로 열어(GPU 약 +0.5GB) 실시간
추론 옆에서 돈다. 둘을 겹쳐 돌리면 실시간 쪽이 밀리고, 어느 결과가 어느 버튼의 것인지도
헷갈린다.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.background import BackgroundTask

import evaluate

log = logging.getLogger(__name__)

EVAL_DIR = Path(os.environ.get("EVAL_DIR", "/eval"))
SET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RUN_NAME = re.compile(r"^\d{8}-\d{6}(-\d+)?$")
# 폴더째 올릴 때 받는 파일. 이미지·정답·클래스 이름 외의 것(Thumbs.db 등)은 조용히 건너뛴다.
UPLOAD_EXT = (*evaluate.IMAGE_EXT, ".txt", ".names")
MAX_SET_MB = 2048
MAX_SET_FILES = 5000
RUNS_SHOWN = 10


class RunIn(BaseModel):
    predict_only: bool = False


def mount(app: FastAPI, cfg) -> None:
    """평가 API 를 앱에 붙인다."""
    lock = threading.Lock()
    # 지금(또는 마지막으로) 돈 평가. 끝난 평가의 결과는 폴더(summary.json)에서 읽는다 —
    # 이것은 '도는 중' 과 '실패' 를 보여 주는 데만 쓴다.
    job: dict = {}

    def _set_dir(name: str) -> Path:
        if not SET_NAME.match(name):
            raise HTTPException(status_code=400, detail="세트 이름이 올바르지 않습니다")
        d = EVAL_DIR / name
        if not d.is_dir():
            raise HTTPException(status_code=404, detail=f"세트가 없습니다: {name}")
        return d

    def _run_dir(set_name: str, run: str) -> Path:
        if not RUN_NAME.match(run):
            raise HTTPException(status_code=400, detail="결과 이름이 올바르지 않습니다")
        d = _set_dir(set_name) / "results" / run
        if not d.is_dir():
            raise HTTPException(status_code=404, detail=f"결과가 없습니다: {run}")
        return d

    def _summary(run_dir: Path) -> dict | None:
        try:
            return json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _describe(d: Path) -> dict:
        _, images = evaluate.find_images(d)
        labels = sum(1 for img in images if evaluate.find_label(img, d))
        runs = []
        results = d / "results"
        if results.is_dir():
            for r in sorted(results.iterdir(), reverse=True):
                s = _summary(r) if r.is_dir() and RUN_NAME.match(r.name) else None
                if s:
                    runs.append({k: s.get(k) for k in
                                 ("run", "mode", "data", "matched", "rate", "pass", "started")})
                if len(runs) >= RUNS_SHOWN:
                    break
        return {"name": d.name, "images": len(images), "labels": labels, "runs": runs}

    # ── 세트 ────────────────────────────────────────────────────────

    @app.get("/api/eval/sets")
    async def list_sets() -> JSONResponse:
        if not EVAL_DIR.is_dir():
            return JSONResponse({"items": [], "job": job or None,
                                 "error": f"평가 폴더({EVAL_DIR})가 붙어 있지 않습니다 — "
                                          ".env 의 EVAL_PATH 를 확인하세요"})

        def scan():
            return [_describe(d) for d in sorted(EVAL_DIR.iterdir())
                    if d.is_dir() and SET_NAME.match(d.name)]

        return JSONResponse({"items": await asyncio.to_thread(scan), "job": job or None})

    @app.post("/api/eval/sets")
    async def upload_set(request: Request) -> JSONResponse:
        """폴더째 올린 것을 새 세트로 둔다.

        파일마다 `paths` 에 상대 경로(webkitRelativePath)를 같이 받는다. 맨 앞 폴더 이름은
        떼고(사람이 고른 폴더 자신) 그 아래 모양(images/, labels/ …)을 그대로 둔다.

        **이미 있는 이름이면 받지 않는다.** 덮어쓰면 옛 정답과 새 이미지가 섞여도 아무도
        모른다. 임시 폴더에 다 받은 뒤 이름을 바꿔 들여놓는다 — 도중에 끊기면 반쪽짜리
        세트가 남지 않는다.
        """
        form = await request.form(max_files=MAX_SET_FILES, max_fields=MAX_SET_FILES + 10)
        name = str(form.get("name") or "").strip()
        if not SET_NAME.match(name):
            raise HTTPException(status_code=400,
                                detail="세트 이름은 영문·숫자·. _ - 로 지어 주세요 (예 kaist-100)")
        if not EVAL_DIR.is_dir():
            raise HTTPException(status_code=503, detail=f"평가 폴더({EVAL_DIR})가 붙어 있지 않습니다")
        dest = EVAL_DIR / name
        if dest.exists():
            raise HTTPException(status_code=409, detail=f"같은 이름의 세트가 이미 있습니다: {name}")

        files = form.getlist("files")
        paths = form.getlist("paths")
        if not files or len(files) != len(paths):
            raise HTTPException(status_code=400, detail="올린 파일이 없습니다")

        tmp = Path(tempfile.mkdtemp(prefix=".upload-", dir=str(EVAL_DIR)))
        kept, skipped, size = 0, 0, 0
        try:
            for f, raw in zip(files, paths):
                rel = _relative(str(raw))
                if rel is None:
                    skipped += 1
                    continue
                target = tmp / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as out:
                    while chunk := await f.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_SET_MB * 1024 * 1024:
                            raise HTTPException(status_code=413,
                                                detail=f"세트가 너무 큽니다 (상한 {MAX_SET_MB}MB)")
                        out.write(chunk)
                kept += 1
            _, images = evaluate.find_images(tmp)
            if not images:
                raise HTTPException(status_code=400,
                                    detail="이미지가 없습니다 — images/ 아래 또는 폴더 바로 아래에 "
                                           "jpg·png 를 두세요")
            tmp.rename(dest)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        log.info("평가 세트 올림: %s (파일 %d개, 건너뜀 %d개, %.1fMB)",
                 name, kept, skipped, size / 1024 / 1024)
        return JSONResponse({**_describe(dest), "skipped": skipped}, status_code=201)

    # ── 실행 ────────────────────────────────────────────────────────

    @app.post("/api/eval/sets/{set_name}/run")
    async def run_set(set_name: str, body: RunIn) -> JSONResponse:
        set_dir = _set_dir(set_name)
        # 입력 문제(정답 일부 빠짐, 깨진 줄 …)는 시작하기 전에 돌려준다. 백그라운드에서
        # 실패시키면 사람은 진행 막대가 사라지는 것만 본다.
        try:
            await asyncio.to_thread(evaluate.load_set, set_dir, predict_only=body.predict_only)
        except evaluate.EvalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

        with lock:
            if job.get("status") == "running":
                raise HTTPException(status_code=409,
                                    detail=f"평가가 이미 돌고 있습니다: {job['set']} "
                                           f"({job['done']}/{job['total'] or '?'})")
            run = datetime.now().strftime("%Y%m%d-%H%M%S")
            out = set_dir / "results" / run
            n = 1
            while out.exists():                       # 같은 초에 두 번 누른 경우
                n += 1
                out = set_dir / "results" / f"{run}-{n}"
            job.clear()
            job.update({"set": set_name, "run": out.name, "status": "running",
                        "done": 0, "total": 0, "current": "", "ok": 0, "ng": 0,
                        "error": None, "started": time.time()})

        def progress(done, total, name, ok):
            job.update(done=done, total=total, current=name)
            if ok is True:
                job["ok"] += 1
            elif ok is False:
                job["ng"] += 1

        def work():
            try:
                evaluate.evaluate(set_dir, cfg, out_dir=out,
                                  predict_only=body.predict_only, progress=progress)
                job["status"] = "done"
                log.info("평가 끝: %s/%s", set_name, out.name)
            except Exception as exc:                                  # noqa: BLE001
                # 반쪽 결과 폴더를 남기지 않는다. 남기면 목록에 숫자 없는 결과가 쌓인다.
                shutil.rmtree(out, ignore_errors=True)
                job.update(status="error", error=str(exc))
                log.exception("평가 실패: %s", set_name)

        threading.Thread(target=work, name=f"eval-{set_name}", daemon=True).start()
        return JSONResponse({"set": set_name, "run": out.name}, status_code=202)

    @app.get("/api/eval/runs/{set_name}/{run}")
    async def get_run(set_name: str, run: str) -> JSONResponse:
        """진행 중이면 진행률, 끝났으면 요약과 이미지별 판정."""
        if job.get("set") == set_name and job.get("run") == run and job["status"] != "done":
            return JSONResponse({"status": job["status"], "job": job})
        run_dir = _run_dir(set_name, run)
        summary = _summary(run_dir)
        if summary is None:
            raise HTTPException(status_code=404, detail="결과 요약(summary.json)이 없습니다")

        def rows():
            with (run_dir / "results.csv").open(encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                next(reader, None)                    # 한글 제목 줄
                return [dict(zip(evaluate.CSV_COLUMNS, r)) for r in reader]

        return JSONResponse({"status": "done", "summary": summary,
                             "rows": await asyncio.to_thread(rows)})

    # ── 결과 보기·내려받기 ──────────────────────────────────────────

    @app.get("/api/eval/runs/{set_name}/{run}/image/{name}")
    async def run_image(set_name: str, run: str, name: str, w: int = 0) -> Response:
        """박스를 그린 결과 이미지. `w` 를 주면 그 폭으로 줄여 준다(격자 썸네일)."""
        folder = _run_dir(set_name, run) / "annotated"
        path = folder / name
        if Path(name).name != name or path.suffix.lower() != ".jpg" or not path.is_file():
            raise HTTPException(status_code=404, detail=f"이미지가 없습니다: {name}")
        if not w:
            return FileResponse(str(path), media_type="image/jpeg")

        def shrink() -> bytes:
            import cv2
            import numpy as np

            img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
            width = max(80, min(int(w), 1920))
            if img.shape[1] > width:
                img = cv2.resize(img, (width, round(img.shape[0] * width / img.shape[1])),
                                 interpolation=cv2.INTER_AREA)
            return cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])[1].tobytes()

        # 결과는 다시 쓰이지 않으므로(실행마다 새 폴더) 브라우저가 오래 들고 있어도 된다.
        return Response(await asyncio.to_thread(shrink), media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/eval/runs/{set_name}/{run}/results.csv")
    async def run_csv(set_name: str, run: str) -> FileResponse:
        path = _run_dir(set_name, run) / "results.csv"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="results.csv 가 없습니다")
        return FileResponse(str(path), media_type="text/csv",
                            filename=f"{set_name}-{run}-results.csv")

    @app.get("/api/eval/runs/{set_name}/{run}/annotated.zip")
    async def run_zip(set_name: str, run: str) -> FileResponse:
        """결과 폴더 통째(박스 이미지·판독 txt·CSV·요약). 시험 보고서에 붙일 묶음이다."""
        run_dir = _run_dir(set_name, run)
        root = f"{set_name}-{run}"

        def build() -> str:
            fd, tmp = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            with zipfile.ZipFile(tmp, "w") as z:
                for p in sorted(run_dir.rglob("*")):
                    if p.is_file():
                        # jpg 는 이미 압축돼 있어 다시 눌러 봐야 시간만 든다
                        kind = zipfile.ZIP_STORED if p.suffix == ".jpg" else zipfile.ZIP_DEFLATED
                        z.write(p, f"{root}/{p.relative_to(run_dir).as_posix()}", compress_type=kind)
            return tmp

        tmp = await asyncio.to_thread(build)
        return FileResponse(tmp, media_type="application/zip", filename=f"{root}.zip",
                            background=BackgroundTask(os.unlink, tmp))


def _relative(raw: str) -> Path | None:
    """올린 파일의 상대 경로를 세트 안의 경로로. 받지 않을 것이면 None.

    맨 앞(사람이 고른 폴더 이름)을 떼고, 두 단계까지만 받는다(images/a.jpg). 결과 폴더,
    숨김 파일, 상위로 올라가는 경로는 받지 않는다.
    """
    parts = [p for p in PurePosixPath(raw.replace("\\", "/")).parts if p not in ("", "/")]
    if len(parts) > 1:
        parts = parts[1:]
    if not parts or len(parts) > 2:
        return None
    if any(p in (".", "..") or p.startswith(".") or p == "results" or ":" in p for p in parts):
        return None
    if not parts[-1].lower().endswith(UPLOAD_EXT):
        return None
    return Path(*parts)

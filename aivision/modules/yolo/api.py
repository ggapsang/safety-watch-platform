"""모듈 자체 API 와 화면 (기본 11990).

왜 모듈이 화면을 직접 갖는가. 이 모듈은 다른 모듈과 성격이 다르다 — 카메라 메타데이터
모듈은 켜 두면 알아서 도는데, 이쪽은 **사람이 할 일이 있다.** 데이터셋을 고르고, 몇 epoch
돌릴지 정하고, 로그를 지켜보고, 다 되면 어느 가중치를 쓸지 고른다.

그것을 플랫폼 화면에 넣으면 코어가 YOLO 를 알게 된다(매니페스토 2번). 그래서 화면도 모듈이
가진다. 플랫폼은 등록된 모듈의 `endpoint` 를 탭으로 감싸 보여 주기만 하고, 그 안에 무엇이
있는지는 모른다 — 다른 모듈이 자기 화면을 들고 와도 똑같이 동작한다.

여기 있는 것은 학습·모델 관리뿐이다. 추론 결과는 이 API 로 돌려주지 않는다. MQTT 로
발행해 인바운드 바인딩을 지난다 — 우리 모듈이라고 특권 통로를 쓰지 않는다(매니페스토 4번).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import training

log = logging.getLogger(__name__)

WEB = Path(__file__).resolve().parent / "web"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class TrainRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    data: str                                  # 데이터셋 yaml 경로
    weights: str = "yolov7_training.pt"        # 사전학습 가중치 (전이학습 출발점)
    hyp: str = "data/hyp.iseco2.yaml"
    cfg: str = "cfg/training/yolov7.yaml"
    epochs: int = Field(default=60, ge=1, le=1000)
    batch: int = Field(default=16, ge=1, le=128)
    imgsz: int = Field(default=640, ge=320, le=1920)
    device: str = ""                           # 비우면 설정값(보통 0 또는 cpu)


class PublishRequest(BaseModel):
    run: str                                   # 학습 이름
    weights: str = "best.pt"
    as_name: str = ""                          # 배치할 파일 이름. 비우면 <run>.onnx
    imgsz: int = Field(default=640, ge=320, le=1920)


def create_app(cfg, trainer: training.Trainer, runner_status) -> FastAPI:
    """`runner_status` 는 추론 워커의 상태를 돌려주는 함수(SDK Runner.status)."""
    app = FastAPI(title="AI Safety Watch — 객체감지 모듈", docs_url="/api/docs",
                  openapi_url="/api/openapi.json")

    models_dir = Path(cfg.models_dir)
    dataset_roots = [Path(p) for p in cfg.dataset_dirs]

    def _run_dir(name: str) -> Path:
        if not SAFE_NAME.match(name):
            raise HTTPException(status_code=400, detail="학습 이름에 쓸 수 없는 문자가 있습니다")
        d = trainer.root / name
        if not d.is_dir():
            raise HTTPException(status_code=404, detail=f"학습을 찾을 수 없습니다: {name}")
        return d

    # ── 상태 ────────────────────────────────────────────────────────

    @app.get("/api/state")
    async def state() -> JSONResponse:
        """화면이 3초마다 부르는 하나의 창구. 조각조각 부르면 화면이 어긋난다."""
        return JSONResponse({
            "module": {"id": cfg.module_id, "name": cfg.module_name,
                       "mode": "dry-run" if cfg.dry_run else "inference",
                       "model": str(cfg.model_path or ""),
                       "conf_thres": cfg.conf_thres, "class_map": cfg.class_map},
            "inference": runner_status(),
            "training": trainer.status(),
            "runs": trainer.runs(),
            "models": training.list_models(models_dir),
            "datasets": training.find_datasets(dataset_roots),
            "pretrained": sorted(p.name for p in models_dir.glob("*.pt")),
        })

    @app.get("/api/logs")
    async def logs(limit: int = 400) -> JSONResponse:
        return JSONResponse({"lines": trainer.log_tail(min(limit, 4000))})

    # ── 학습 ────────────────────────────────────────────────────────

    @app.post("/api/train")
    async def start_training(body: TrainRequest) -> JSONResponse:
        if not SAFE_NAME.match(body.name):
            raise HTTPException(status_code=400,
                                detail="학습 이름은 영문·숫자·. _ - 만 쓸 수 있습니다")
        weights = body.weights
        if weights:
            # 사전학습 가중치는 모델 폴더에서 찾는다. 절대경로를 그대로 받지 않는다.
            candidate = models_dir / Path(weights).name
            if not candidate.is_file():
                raise HTTPException(
                    status_code=400,
                    detail=f"사전학습 가중치가 없습니다: {candidate}. "
                           "공식 릴리스에서 yolov7_training.pt 를 받아 models 폴더에 두세요.")
            weights = str(candidate)
        try:
            run = trainer.start(name=body.name, data=body.data, weights=weights,
                                hyp=body.hyp, cfg=body.cfg, epochs=body.epochs,
                                batch=body.batch, imgsz=body.imgsz,
                                device=body.device or cfg.train_device)
        except (RuntimeError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return JSONResponse(run.to_dict(), status_code=201)

    @app.post("/api/train/cancel")
    async def cancel_training() -> JSONResponse:
        if not trainer.cancel():
            raise HTTPException(status_code=409, detail="돌고 있는 학습이 없습니다")
        return JSONResponse({"canceled": True})

    @app.delete("/api/runs/{name}")
    async def delete_run(name: str) -> JSONResponse:
        d = _run_dir(name)
        if trainer.busy and trainer._run and trainer._run.name == name:   # noqa: SLF001
            raise HTTPException(status_code=409, detail="돌고 있는 학습은 지울 수 없습니다")
        import shutil

        shutil.rmtree(d, ignore_errors=True)
        log.info("학습 산출물 삭제: %s", name)
        return JSONResponse({"deleted": name})

    # ── 모델 ────────────────────────────────────────────────────────

    @app.post("/api/publish")
    async def publish_model(body: PublishRequest) -> JSONResponse:
        """학습 결과를 ONNX 로 내보내 추론이 쓰는 자리에 둔다."""
        d = _run_dir(body.run)
        pt = d / "weights" / Path(body.weights).name
        if not pt.is_file():
            raise HTTPException(status_code=404, detail=f"가중치가 없습니다: {pt.name}")
        try:
            onnx = training.export_onnx(pt, body.imgsz)
        except (RuntimeError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        name = body.as_name or f"{body.run}.onnx"
        if not SAFE_NAME.match(name) or not name.endswith(".onnx"):
            raise HTTPException(status_code=400, detail="모델 파일 이름이 올바르지 않습니다")
        dest = training.publish(onnx, models_dir, name)
        return JSONResponse({"model": dest.name, "size_mb": round(
            dest.stat().st_size / 1024 / 1024, 1)})

    @app.get("/api/runs/{name}/results")
    async def run_results(name: str) -> FileResponse:
        """학습 곡선 이미지(results.png). 있으면 화면에 띄운다."""
        d = _run_dir(name)
        for candidate in ("results.png", "results.jpg"):
            p = d / candidate
            if p.is_file():
                return FileResponse(str(p))
        raise HTTPException(status_code=404, detail="결과 그림이 아직 없습니다")

    # ── 화면 ────────────────────────────────────────────────────────

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(WEB / "index.html"))

    @app.get("/app.js")
    async def script() -> FileResponse:
        return FileResponse(str(WEB / "app.js"), media_type="application/javascript")

    return app

"""모듈 자체 API 와 화면 (기본 11990).

**왜 모듈이 화면을 직접 갖는가.** 이 모듈은 켜 두면 알아서 도는 물건이 아니다. 사람이
할 일이 있다 — 모델을 올리고, 그 모델이 무엇을 잡는지 확인해 이름을 붙이고, 플랫폼
탐지 항목에 연결하고, 민감도를 맞춘다.

그것을 플랫폼 화면에 넣으면 코어가 YOLO 를 알게 된다(매니페스토 2번). 그래서 화면도
모듈이 가진다. 플랫폼은 등록된 모듈의 `endpoint` 를 탭으로 감싸 보여 주기만 하고, 그
안에 무엇이 있는지는 모른다 — 다른 모듈이 자기 화면을 들고 와도 똑같이 동작한다.

**추론 결과는 이 API 로 돌려주지 않는다.** MQTT 로 발행해 인바운드 바인딩을 지난다 —
우리 모듈이라고 특권 통로를 쓰지 않는다(매니페스토 4번). 여기 있는 것은 모델과 설정뿐이다.

설정을 바꾸면 `reload_workers()` 로 추론 워커를 다시 띄운다. 모델을 바꾸고 컨테이너를
재시작하게 만들면 현장에서 아무도 안 바꾼다.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import config as config_module
import inference
import settings as settings_module

log = logging.getLogger(__name__)

WEB = Path(__file__).resolve().parent / "web"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# 업로드 상한. 이보다 큰 YOLO ONNX 는 흔치 않고, 상한이 없으면 디스크가 조용히 찬다.
MAX_UPLOAD_MB = 512


class ClassRowIn(BaseModel):
    key: str
    alias: str = ""
    item: str = ""


class ClassesIn(BaseModel):
    rows: list[ClassRowIn]


class TuningIn(BaseModel):
    conf_thres: float | None = None
    iou_thres: float | None = None
    min_conf: float | None = None
    min_box_px: float | None = None
    sample_fps: float | None = None


class NoteIn(BaseModel):
    note: str = ""


def create_app(cfg, runner_status: Callable[[], dict],
               reload_workers: Callable[[], None]) -> FastAPI:
    """`runner_status` 는 추론 워커 상태(SDK Runner.status), `reload_workers` 는
    설정을 바꾼 뒤 워커를 새 설정으로 다시 띄우는 함수다."""
    app = FastAPI(title="AI Safety Watch — 객체감지 모듈", docs_url="/api/docs",
                  openapi_url="/api/openapi.json")

    models_dir = Path(cfg.models_dir)

    def _load() -> settings_module.Settings:
        return settings_module.load(models_dir)

    def _commit(s: settings_module.Settings) -> None:
        """저장하고 곧바로 반영한다. 저장과 반영을 나누면 '눌렀는데 안 바뀐다' 가 된다."""
        settings_module.save(models_dir, s)
        config_module.apply_settings(cfg, s)
        reload_workers()

    def _model_file(name: str) -> Path:
        if not SAFE_NAME.match(name) or not name.endswith(".onnx"):
            raise HTTPException(status_code=400, detail="모델 파일 이름이 올바르지 않습니다")
        path = models_dir / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"모델을 찾을 수 없습니다: {name}")
        return path

    def _models(s: settings_module.Settings) -> list[dict]:
        active = cfg.model_path.name if cfg.model_path else ""
        out = []
        for p in sorted(models_dir.glob("*.onnx")):
            rows = s.rows(p.name)
            out.append({
                "name": p.name,
                "size_mb": round(p.stat().st_size / 1024 / 1024, 1),
                "active": p.name == active,
                "note": s.notes.get(p.name, ""),
                "classes": [r.to_dict() for r in rows],
                # 표가 아직 없으면 화면이 '클래스를 못 읽었다' 와 '아직 안 열어 봤다' 를
                # 구분해 보여 줄 수 있어야 한다.
                "inspected": bool(rows),
            })
        return out

    # ── 상태 ────────────────────────────────────────────────────────

    @app.get("/api/state")
    async def state() -> JSONResponse:
        """화면이 주기적으로 부르는 하나의 창구. 조각조각 부르면 화면이 어긋난다."""
        s = _load()
        return JSONResponse({
            "module": {
                "id": cfg.module_id, "name": cfg.module_name,
                "mode": ("stopped" if cfg.stopped
                         else "dry-run" if cfg.dry_run else "inference"),
                # 실제로 있는 파일만 이름을 보여 준다. MODEL_PATH 는 '있었으면 하는 곳'
                # 이기도 해서, 없는 이름을 띄우면 올린 줄 알고 왜 dry-run 인지 묻게 된다.
                "model": (cfg.model_path.name
                          if cfg.model_path and cfg.model_path.is_file() else ""),
                "imgsz": cfg.imgsz, "layout": cfg.layout, "device": cfg.device,
            },
            "inference": runner_status(),
            "tuning": {name: getattr(cfg, name) for name in settings_module.TUNING},
            "limits": {name: {"min": lo, "max": hi}
                       for name, (lo, hi) in settings_module.TUNING.items()},
            "models": _models(s),
        })

    @app.get("/api/solutions")
    async def solutions() -> JSONResponse:
        """플랫폼의 탐지 항목 목록. 클래스를 무엇에 연결할지 고르는 드롭다운이 쓴다.

        모듈이 플랫폼을 조회만 한다 — 코어는 이 모듈이 무엇을 하는지 여전히 모른다.
        플랫폼이 안 보여도 화면이 죽으면 안 되므로 빈 목록으로 내려앉는다.
        """
        import json
        import urllib.request

        url = f"{cfg.platform_url.rstrip('/')}/api/solutions"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:      # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:                                      # noqa: BLE001
            log.warning("탐지 항목을 가져오지 못했습니다 (%s): %s", url, exc)
            return JSONResponse({"items": [], "error": "플랫폼에 연결하지 못했습니다"})
        return JSONResponse({"items": [
            {"code": x.get("code"), "name": x.get("short_name") or x.get("name")}
            for x in data if isinstance(x, dict)
        ]})

    # ── 모델 ────────────────────────────────────────────────────────

    @app.post("/api/models")
    async def upload_model(file: UploadFile = File(...)) -> JSONResponse:
        """ONNX 파일을 받아 모델 폴더에 둔다.

        임시 파일에 받아 **열리는지 확인한 다음에** 옮긴다. 곧바로 모델 폴더에 쓰면
        깨진 파일이 남고, 워커가 뜰 때마다 죽는데 그 이유는 화면에 안 나온다.
        """
        name = Path(file.filename or "").name
        if not SAFE_NAME.match(name) or not name.endswith(".onnx"):
            raise HTTPException(
                status_code=400,
                detail="파일 이름은 영문·숫자·. _ - 로 된 .onnx 여야 합니다")

        models_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="upload-", dir=str(models_dir))) / name
        size = 0
        try:
            with tmp.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_MB * 1024 * 1024:
                        raise HTTPException(
                            status_code=413,
                            detail=f"모델 파일이 너무 큽니다 (상한 {MAX_UPLOAD_MB}MB)")
                    out.write(chunk)
            try:
                names, count = inference.inspect(tmp)
            except Exception as exc:                                  # noqa: BLE001
                log.warning("업로드한 모델을 열지 못했습니다 (%s): %s", name, exc)
                raise HTTPException(
                    status_code=400,
                    detail=f"ONNX 로 열리지 않습니다: {exc}") from None
            tmp.replace(models_dir / name)
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)

        # 이름을 못 읽었으면 인덱스로 표를 만든다 — 사람이 화면에서 이름을 붙인다.
        keys = names or [str(i) for i in range(count)]
        s = _load()
        s.ensure_rows(name, keys)
        if not s.active_model:
            # 첫 모델이면 바로 쓴다. 올려 두고 또 '사용' 을 눌러야 하면 왜 안 도는지 모른다.
            s.active_model = name
        _commit(s)
        log.info("모델 업로드: %s (%.1fMB · 클래스 %d개%s)", name,
                 size / 1024 / 1024, len(keys), "" if names else " · 이름 없음")
        return JSONResponse({"name": name, "classes": keys,
                             "named": bool(names)}, status_code=201)

    @app.post("/api/models/{name}/use")
    async def use_model(name: str) -> JSONResponse:
        """이 모델로 추론을 시작한다. 꺼 둔 상태였다면 함께 켠다."""
        _model_file(name)
        s = _load()
        s.active_model = name
        s.stopped = False
        _commit(s)
        log.info("적용 모델 변경: %s", name)
        return JSONResponse({"active_model": name, "stopped": False})

    @app.post("/api/models/{name}/unuse")
    async def unuse_model(name: str) -> JSONResponse:
        """추론을 멈춘다. 모델은 그대로 두고 쓰지 않을 뿐이다.

        모델 선택(active_model)을 지우지 않는 이유: 다시 켤 때 무엇을 쓸지 사람이 또
        고르게 하면 번거롭다. '무엇을 쓸지' 와 '지금 쓸지' 는 다른 결정이다.
        """
        _model_file(name)
        s = _load()
        if s.active_model and s.active_model != name:
            raise HTTPException(status_code=409,
                                detail=f"지금 쓰는 모델이 아닙니다 (사용 중: {s.active_model})")
        s.stopped = True
        _commit(s)
        log.info("추론 중단: %s", name)
        return JSONResponse({"active_model": s.active_model, "stopped": True})

    @app.post("/api/models/{name}/inspect")
    async def inspect_model(name: str) -> JSONResponse:
        """모델을 다시 열어 클래스 표를 맞춘다. 같은 이름으로 새 모델을 덮어썼을 때 쓴다."""
        path = _model_file(name)
        try:
            names, count = inference.inspect(path)
        except Exception as exc:                                      # noqa: BLE001
            raise HTTPException(status_code=400,
                                detail=f"ONNX 로 열리지 않습니다: {exc}") from None
        s = _load()
        s.ensure_rows(name, names or [str(i) for i in range(count)])
        _commit(s)
        return JSONResponse({"classes": [r.to_dict() for r in s.rows(name)],
                             "named": bool(names)})

    @app.delete("/api/models/{name}")
    async def delete_model(name: str) -> JSONResponse:
        path = _model_file(name)
        s = _load()
        # 중단해 둔 모델은 지울 수 있다. 돌고 있는 것만 막으면 된다 —
        # 쓰지도 않는 모델을 지우려고 다른 모델을 먼저 적용하게 만들 이유가 없다.
        in_use = (not s.stopped) and (s.active_model == name
                                      or (cfg.model_path and cfg.model_path.name == name))
        if in_use:
            raise HTTPException(status_code=409,
                                detail="쓰고 있는 모델은 지울 수 없습니다. "
                                       "먼저 '사용 중단' 하거나 다른 모델을 적용하세요.")
        path.unlink()
        s.models.pop(name, None)
        s.notes.pop(name, None)
        settings_module.save(models_dir, s)
        log.info("모델 삭제: %s", name)
        return JSONResponse({"deleted": name})

    # ── 클래스 표 ───────────────────────────────────────────────────

    @app.put("/api/models/{name}/classes")
    async def set_classes(name: str, body: ClassesIn) -> JSONResponse:
        """클래스마다 '표시 이름' 과 '플랫폼 탐지 항목' 을 정한다.

        둘을 하나로 합치지 않는 이유: 항목을 비워 둔 채 이름만 붙이는 경우가 실제로
        있다. 박스는 보고 싶지만 이벤트로 쌓고 싶지는 않은 클래스(사람·차량 같은
        배경 객체)가 그렇다.
        """
        _model_file(name)
        s = _load()
        known = {r.key for r in s.rows(name)}
        rows = [settings_module.ClassRow(key=r.key, alias=r.alias.strip(),
                                         item=r.item.strip())
                for r in body.rows if r.key in known]
        if len(rows) != len(body.rows):
            raise HTTPException(status_code=400,
                                detail="모델에 없는 클래스가 섞여 있습니다. "
                                       "화면을 새로 고친 뒤 다시 저장하세요.")
        s.models[name] = rows
        _commit(s)
        return JSONResponse({"classes": [r.to_dict() for r in rows]})

    @app.put("/api/models/{name}/note")
    async def set_note(name: str, body: NoteIn) -> JSONResponse:
        """모델에 붙이는 메모. 어디서 학습한 것인지, 무엇을 잡는 모델인지 적어 둔다."""
        _model_file(name)
        s = _load()
        s.notes[name] = body.note.strip()[:500]
        settings_module.save(models_dir, s)
        return JSONResponse({"note": s.notes[name]})

    # ── 조정값 ──────────────────────────────────────────────────────

    @app.put("/api/tuning")
    async def set_tuning(body: TuningIn) -> JSONResponse:
        s = _load()
        s.set_tuning(body.model_dump(exclude_none=True))
        _commit(s)
        log.info("조정값 변경: %s", s.tuning)
        return JSONResponse({name: getattr(cfg, name) for name in settings_module.TUNING})

    # ── 화면 ────────────────────────────────────────────────────────

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(WEB / "index.html"))

    @app.get("/app.js")
    async def script() -> FileResponse:
        return FileResponse(str(WEB / "app.js"), media_type="application/javascript")

    return app

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
# 한 카메라에 걸 수 있는 모델 수. 모델마다 ONNX 세션과 추론 한 번이 더 붙고, 그것이
# 카메라 수와 곱해진다 — 상한이 없으면 화면에서 클릭 몇 번으로 GPU 를 재울 수 있다.
MAX_MODELS_PER_CAMERA = 4


class ClassRowIn(BaseModel):
    key: str
    alias: str = ""
    memo: str = ""
    item: str = ""
    enabled: bool = True


class ClassesIn(BaseModel):
    rows: list[ClassRowIn]


class TuningIn(BaseModel):
    conf_thres: float | None = None
    iou_thres: float | None = None
    min_conf: float | None = None
    min_box_px: float | None = None
    sample_fps: float | None = None


class CameraModelsIn(BaseModel):
    models: list[str] = []
    off: bool = False


class NoteIn(BaseModel):
    note: str = ""


def create_app(cfg, runner_status: Callable[[], dict],
               reload_workers: Callable[[], None]) -> FastAPI:
    """`runner_status` 는 추론 워커 상태(SDK Runner.status), `reload_workers` 는
    설정을 바꾼 뒤 워커를 새 설정으로 다시 띄우는 함수다."""
    app = FastAPI(title="AI Safety Watcher — 객체감지 모듈", docs_url="/api/docs",
                  openapi_url="/api/openapi.json")

    models_dir = Path(cfg.models_dir)

    def _load() -> settings_module.Settings:
        return settings_module.load(models_dir)

    def _commit(s: settings_module.Settings) -> None:
        """저장하고 곧바로 반영한다. 저장과 반영을 나누면 '눌렀는데 안 바뀐다' 가 된다."""
        settings_module.save(models_dir, s)
        config_module.apply_settings(cfg, s)
        reload_workers()

    async def _platform_json(path: str, what: str):
        """플랫폼에서 JSON 을 받아 온다. 못 받으면 None.

        **다른 스레드에서 받는다.** urllib 은 블로킹이라 async 함수 안에서 그냥 부르면
        받는 동안 uvicorn 의 이벤트 루프가 통째로 멈춘다 — 플랫폼이 안 뜰 때 이 한 줄이
        타임아웃 5초를 잡아먹고, 그동안 이 모듈 화면의 모든 요청이 같이 선다.

        플랫폼이 안 보여도 화면은 살아 있어야 하므로 예외를 삼키고 None 으로 내려앉는다.
        """
        import asyncio
        import json
        import urllib.request

        url = f"{cfg.platform_url.rstrip('/')}{path}"

        def fetch():
            with urllib.request.urlopen(url, timeout=5) as resp:      # noqa: S310
                return json.loads(resp.read().decode("utf-8"))

        try:
            return await asyncio.to_thread(fetch)
        except Exception as exc:                                      # noqa: BLE001
            log.warning("%s 를 가져오지 못했습니다 (%s): %s", what, url, exc)
            return None

    def _model_file(name: str) -> Path:
        if not SAFE_NAME.match(name) or not name.endswith(".onnx"):
            raise HTTPException(status_code=400, detail="모델 파일 이름이 올바르지 않습니다")
        path = models_dir / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"모델을 찾을 수 없습니다: {name}")
        return path

    def _common_names() -> list[str]:
        """지금 실제로 공통 모델로 도는 것들(파일이 있는 것만)."""
        return [p.name for p in cfg.common_models]

    def _models(s: settings_module.Settings) -> list[dict]:
        active = set(_common_names())
        out = []
        for p in sorted(models_dir.glob("*.onnx")):
            rows = s.rows(p.name)
            out.append({
                "name": p.name,
                "size_mb": round(p.stat().st_size / 1024 / 1024, 1),
                # 공통 모델로 켜져 있나. 여럿이 동시에 켜질 수 있다.
                "active": p.name in active,
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
                # idle: 켜 둔 공통 모델도 없고 카메라별로 고른 모델도 없다 — 아무것도 안 돈다.
                "mode": ("dry-run" if cfg.dry_run
                         else "idle" if not cfg.models_in_use() else "inference"),
                # 실제로 있는 파일만 이름을 보여 준다. 없는 이름을 띄우면 올린 줄 알고
                # 왜 안 도는지 묻게 된다.
                "models": _common_names(),
                "model": ", ".join(_common_names()),
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
        data = await _platform_json("/api/solutions", "탐지 항목")
        if data is None:
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
        if s.active_models is None:
            # 처음 올린 모델이면 바로 쓴다. 올려 두고 또 '사용' 을 눌러야 하면 왜 안 도는지
            # 모른다. 이미 고른 적이 있으면 건드리지 않는다 — 일부러 다 꺼 둔 현장에서
            # 모델 하나 올렸다고 저절로 켜지면 안 된다.
            s.active_models = [name]
        _commit(s)
        log.info("모델 업로드: %s (%.1fMB · 클래스 %d개%s)", name,
                 size / 1024 / 1024, len(keys), "" if names else " · 이름 없음")
        return JSONResponse({"name": name, "classes": keys,
                             "named": bool(names)}, status_code=201)

    @app.get("/api/cameras")
    async def cameras() -> JSONResponse:
        """이 모듈이 담당하는 카메라와, 각 카메라에 걸린 모델.

        담당 목록은 플랫폼이 정한다(관리자/플러그인 탭에서 할당). 여기서는 그것을 받아
        '어느 카메라에 어느 모델' 만 더한다 — 할당까지 여기서 하면 같은 일을 두 곳에서
        하게 되고, 어느 쪽이 맞는지 아무도 모르게 된다.
        """
        s = _load()
        work = await _platform_json(f"/api/modules/{cfg.module_id}/work", "일감")
        if work is None:
            return JSONResponse({"items": [], "error": "플랫폼에 연결하지 못했습니다"})

        common = _common_names()

        def row(w: dict) -> dict:
            key = str(w.get("camera_id"))
            picked = s.camera_models.get(key, [])
            off = key in s.camera_off
            return {
                "camera_id": w.get("camera_id"),
                "name": w.get("camera_name") or "",
                "location": w.get("location") or "",
                # 비면 공통 모델을 쓴다는 뜻이다. 화면이 그것을 '(공통)' 으로 보인다.
                "models": picked,
                "off": off,
                # 실제로 지금 돌고 있는 모델. 세 상태(끔·공통·직접 고름)를 화면이 매번
                # 다시 계산하지 않게 여기서 한 번만 정한다. 공통 모델을 다 꺼 뒀고 따로
                # 고른 것도 없으면 빈 목록 — 화면이 '모델 없음' 으로 보인다.
                "effective": [] if off else (picked or list(common)),
            }

        return JSONResponse({"items": [row(w) for w in (work.get("items") or [])],
                             "common_models": common})

    @app.put("/api/cameras/{camera_id}/models")
    async def set_camera_models(camera_id: int, body: CameraModelsIn) -> JSONResponse:
        """이 카메라에 걸 모델들을 정한다.

        세 상태가 있다. `off` 면 이 카메라는 추론하지 않는다(영상도 안 연다). 목록이
        비어 있으면 공통 모델을 쓴다. 이름을 골랐으면 그 모델들을 **모두** 돌린다.

        '비움' 을 '끔' 으로 쓰지 않는 이유는 settings.Settings.camera_off 주석에 있다 —
        정반대인 두 뜻을 한 칸에 담을 수 없다.
        """
        names = list(dict.fromkeys(body.models))       # 순서는 지키고 중복만 없앤다
        if len(names) > MAX_MODELS_PER_CAMERA:
            # 개수를 파일 확인보다 먼저 본다. 뒤에 두면 다섯 개를 보냈을 때 '첫 번째
            # 모델을 못 찾겠다' 는 엉뚱한 이유가 돌아간다.
            raise HTTPException(
                status_code=400,
                detail=f"한 카메라에 모델은 {MAX_MODELS_PER_CAMERA}개까지 걸 수 있습니다")
        for name in names:
            _model_file(name)                          # 없는 모델이면 여기서 404

        s = _load()
        key = str(camera_id)
        if body.off:
            s.camera_off.add(key)
        else:
            s.camera_off.discard(key)
        if names:
            s.camera_models[key] = names
        else:
            s.camera_models.pop(key, None)
        _commit(s)
        log.info("카메라 %d: %s", camera_id,
                 "사용 안 함" if body.off else (", ".join(names) or "(공통)"))
        return JSONResponse({"camera_id": camera_id, "models": names, "off": body.off})

    @app.post("/api/models/{name}/use")
    async def use_model(name: str) -> JSONResponse:
        """이 모델을 공통 모델에 **더한다.** 이미 켜 둔 다른 모델은 그대로 둔다.

        예전에는 공통 모델이 하나라 '사용' 이 곧 '바꾸기' 였다 — 두 번째 모델을 켜면 첫
        번째가 꺼져서, 둘 다 켜지는 줄 안 사람을 헷갈리게 했다.
        """
        _model_file(name)
        s = _load()
        if s.active_models is None:
            # 한 번도 고른 적이 없던 현장 — env 의 MODEL_PATH 로 돌던 것도 이어서 켜 둔다.
            # 안 그러면 두 번째 모델을 켜는 순간 돌던 것이 꺼진다(바로 그 문제다).
            s.active_models = [p.name for p in cfg.common_models]
        if name not in s.active_models:
            s.active_models.append(name)
        _commit(s)
        log.info("공통 모델 켬: %s (지금 %s)", name, ", ".join(s.active_models))
        return JSONResponse({"active_models": s.active_models})

    @app.post("/api/models/{name}/unuse")
    async def unuse_model(name: str) -> JSONResponse:
        """이 모델만 공통 모델에서 뺀다. 파일과 클래스 표는 그대로다.

        다 빼면 '일부러 다 껐다' 로 남는다(빈 목록) — 따로 모델을 고르지 않은 카메라는
        쉬고, 카메라별로 고른 카메라는 그대로 돈다. 재시작해도 env 의 모델로 되살아나지
        않는다(settings.Settings.active_models 주석 참조).
        """
        _model_file(name)
        s = _load()
        if s.active_models is None:
            s.active_models = [p.name for p in cfg.common_models]
        s.active_models = [n for n in s.active_models if n != name]
        _commit(s)
        log.info("공통 모델 끔: %s (남은 것 %s)", name, ", ".join(s.active_models) or "없음")
        return JSONResponse({"active_models": s.active_models})

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
        # 공통 모델로 켜 둔 것만 막는다. 꺼 둔 모델을 지우려고 다른 것을 먼저 손대게 만들
        # 이유가 없다. 카메라별로만 걸어 둔 모델은 지우면서 그 카메라에서 함께 뺀다(아래).
        if name in _common_names():
            raise HTTPException(status_code=409,
                                detail="공통 모델로 켜 둔 모델은 지울 수 없습니다. "
                                       "먼저 '사용 중단' 하세요.")
        path.unlink()
        s.models.pop(name, None)
        # 이 모델을 걸어 둔 카메라가 있으면 거기서만 뺀다. 안 그러면 없는 파일을 가리킨 채
        # 남아, 그 카메라만 조용히 멈춘다. 같이 걸어 둔 다른 모델은 그대로 둔다 —
        # 하나를 지웠다고 그 카메라가 통째로 눈을 감으면 안 된다.
        for cam, names in list(s.camera_models.items()):
            if name not in names:
                continue
            rest = [n for n in names if n != name]
            if rest:
                s.camera_models[cam] = rest
            else:
                s.camera_models.pop(cam, None)
            log.info("카메라 %s 에서 %s 지정을 풉니다 (모델 삭제) — 남은 모델: %s",
                     cam, name, ", ".join(rest) or "(공통)")
        s.notes.pop(name, None)
        # 저장만 하지 않고 반영한다. 카메라별 지정에서 이 모델을 뺐으니 그 카메라의 워커가
        # 다시 떠야 한다 — 안 그러면 지운 파일의 세션을 메모리에 쥔 채 계속 돈다.
        _commit(s)
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
                                         memo=r.memo.strip()[:500], item=r.item.strip(),
                                         enabled=r.enabled)
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

    # ── 이미지 판별률 평가 ──────────────────────────────────────────
    # 시험용 기능이라 파일을 따로 둔다(eval_api.py). 판독 코어는 evaluate.py 하나다.
    import eval_api
    eval_api.mount(app, cfg)

    # ── 화면 ────────────────────────────────────────────────────────

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(WEB / "index.html"))

    @app.get("/app.js")
    async def script() -> FileResponse:
        return FileResponse(str(WEB / "app.js"), media_type="application/javascript")

    return app

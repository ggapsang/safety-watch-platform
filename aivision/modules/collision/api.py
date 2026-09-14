"""모듈 자체 API 와 화면 (기본 12010).

**왜 모듈이 화면을 직접 갖는가.** 이 모듈에는 사람이 할 일이 있다 — 바닥 4점을 찍어
호모그래피를 잡고, 어떤 라벨이 AMR 이고 사람인지 알려 주고, 위험 임계값을 현장에 맞춘다.
그것을 플랫폼 화면에 넣으면 코어가 충돌 판정을 알게 된다(계약 4장). 플랫폼은 등록된
`endpoint` 를 탭으로 감싸기만 하고 그 안에 무엇이 있는지 모른다.

**판정 결과는 이 API 로 돌려주지 않는다.** MQTT 로 발행해 인바운드 바인딩을 지난다.
여기 있는 것은 설정과 '지금 무슨 일이 일어나고 있나' 뿐이다.

**플랫폼 조회는 여기서 중계한다.** 화면은 모듈 주소에서 떠 있어 플랫폼과 오리진이
다르다. 브라우저가 막으므로 `/api/solutions`·`/api/cameras`·스냅샷을 이 모듈이 대신
불러 준다. 플랫폼이 안 보이면 빈 목록으로 내려앉는다 — 화면이 죽으면 안 된다.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import (FileResponse, JSONResponse, Response,
                               StreamingResponse)
from pydantic import BaseModel

import config as config_module
import geometry
import settings as settings_module

log = logging.getLogger(__name__)

WEB = Path(__file__).resolve().parent / "web"
# 보정 오차가 이보다 크면 저장은 하되 화면이 경고한다. 30cm 는 사람 반경과 같은 크기라,
# 이보다 틀린 보정으로는 '통로 안인가' 를 가릴 수 없다.
WARN_ERROR_M = 0.3


class LabelsIn(BaseModel):
    amr: list[str] = []
    person: list[str] = []


class ItemsIn(BaseModel):
    risk: str = ""
    collision: str = ""


class PointIn(BaseModel):
    u: float
    v: float
    x: float
    y: float


class CalibrationIn(BaseModel):
    points: list[PointIn]


class CameraIn(BaseModel):
    enabled: bool | None = None
    note: str | None = None


class OverlayIn(BaseModel):
    publish_live: bool | None = None
    zones: bool | None = None


def create_app(cfg: config_module.Config, service) -> FastAPI:
    app = FastAPI(title="AI Safety Watch — 충돌 위험 모듈", docs_url="/api/docs",
                  openapi_url="/api/openapi.json")
    config_dir = Path(cfg.config_dir)

    def _commit() -> None:
        """저장하고 곧바로 반영한다. 저장과 반영을 나누면 '눌렀는데 안 바뀐다' 가 된다."""
        settings_module.save(config_dir, service.settings)
        service.reload()

    def _setup(camera_id: int) -> settings_module.CameraSetup:
        s = service.settings
        if camera_id not in s.cameras:
            s.cameras[camera_id] = settings_module.CameraSetup()
        return s.cameras[camera_id]

    # ── 상태 ────────────────────────────────────────────────────────

    @app.get("/api/state")
    async def state() -> JSONResponse:
        """화면이 주기적으로 부르는 하나의 창구. 조각조각 부르면 화면이 어긋난다."""
        s = service.settings
        status = service.status()
        per_camera = {c["camera_id"]: c for c in status["per_camera"]}
        assigned = {w.camera_id: w for w in service.work}

        cameras = []
        for cam in sorted(set(assigned) | set(s.cameras)):
            setup = s.setup(cam)
            H = setup.homography()
            work = assigned.get(cam)
            cameras.append({
                "camera_id": cam,
                "camera_name": work.camera_name if work else "",
                "assigned": cam in assigned,
                "enabled": setup.enabled,
                "note": setup.note,
                "points": [p.to_dict() for p in setup.points],
                "calibrated": H is not None,
                "error_m": (round(geometry.reprojection_error(H, setup.pairs()), 3)
                            if H is not None else None),
                "runtime": per_camera.get(cam),
            })

        return JSONResponse({
            "module": {
                "id": cfg.module_id, "name": cfg.module_name,
                "public_url": cfg.public_url,
                "items": {"risk": cfg.risk_item, "collision": cfg.collision_item},
                "labels": {"amr": cfg.amr_labels, "person": cfg.person_labels},
                "sources": cfg.src_modules,
                "publish_live": cfg.publish_live,
                "overlay_zones": cfg.overlay_zones,
                "tick_hz": cfg.tick_hz,
            },
            "service": status,
            "platform_ok": service.platform_seen,
            "tuning": cfg.tuning,
            "limits": {name: {"min": lo, "max": hi, "default": default}
                       for name, (lo, hi, default) in settings_module.TUNING.items()},
            "cameras": cameras,
            "judgements": [asdict(j) for j in list(service.judgements)[:60]],
            "warn_error_m": WARN_ERROR_M,
        })

    @app.get("/api/solutions")
    async def solutions() -> JSONResponse:
        """플랫폼의 탐지 항목 목록. 무엇에 연결할지 고르는 드롭다운이 쓴다."""
        data = service.platform_json("/api/solutions")
        if data is None:
            return JSONResponse({"items": [], "error": "플랫폼에 연결하지 못했습니다"})
        return JSONResponse({"items": [
            {"code": x.get("code"), "name": x.get("short_name") or x.get("name")}
            for x in data if isinstance(x, dict)]})

    @app.get("/api/cameras")
    async def cameras() -> JSONResponse:
        """전체 카메라 목록(계약 4장). 담당이 아닌 카메라를 미리 보정해 둘 때 쓴다."""
        data = service.platform_json("/api/cameras")
        if data is None:
            return JSONResponse({"items": [], "error": "플랫폼에 연결하지 못했습니다"})
        rows = data if isinstance(data, list) else data.get("items") or []
        return JSONResponse({"items": [
            {"id": x.get("id"), "name": x.get("name") or "",
             "location": x.get("location") or ""}
            for x in rows if isinstance(x, dict)]})

    @app.get("/api/cameras/{camera_id}/snapshot.jpg")
    async def snapshot(camera_id: int) -> Response:
        """보정 화면이 점을 찍는 배경. 플랫폼 스냅샷을 그대로 중계한다."""
        got = service.platform_bytes(f"/api/stream/{camera_id}/snapshot.jpg")
        if got is None:
            raise HTTPException(status_code=502, detail="스냅샷을 가져오지 못했습니다")
        data, content_type = got
        # 캐시하지 않는다. 보정할 때는 '지금 화면' 이어야 한다.
        return Response(content=data, media_type=content_type,
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/cameras/{camera_id}/stream")
    async def stream(camera_id: int) -> StreamingResponse:
        """플랫폼 MJPEG 중계. 미리보기 화면이 이 위에 구역을 겹쳐 그린다.

        스냅샷을 주기적으로 새로 받는 방법도 있지만, 위험 구역은 **AMR 이 움직이는
        동안** 늘었다 줄었다 하는 그림이다. 정지 화면 위에 그리면 그 변화가 안 보인다.
        """
        got = service.platform_stream(f"/api/stream/{camera_id}")
        if got is None:
            raise HTTPException(status_code=502, detail="스트림을 열지 못했습니다")
        chunks, content_type = got
        return StreamingResponse(chunks, media_type=content_type,
                                 headers={"Cache-Control": "no-store"})

    # ── 그림 (구역 오버레이) ────────────────────────────────────────

    @app.get("/api/overlay/{camera_id}")
    async def overlay(camera_id: int) -> JSONResponse:
        """지금 그릴 것 한 벌 — 트랙 박스 · 구역 다각형 · 구역 박스.

        **이 모듈의 화면이 쓰는 창구이자, 남이 쓰라고 열어 둔 창구다.** 브로커로 나가는
        라이브(`aivision/live`)와 같은 내용이지만 이쪽은 `publish_live` 설정과 무관하게
        늘 열려 있다 — 자기 화면에서 보는 것과 남에게 보내는 것은 다른 결정이다.

        좌표는 둘 다 0~1 정규화다. `zone_boxes` 는 계약이 아는 축정렬 사각형이고,
        `zones[].points` 는 원근이 살아 있는 다각형이다.
        """
        got = service.overlay(camera_id)
        if got is None:
            # 담당이 아닌 카메라다. 404 대신 빈 그림을 준다 — 화면이 '아직 할당 전' 을
            # 오류로 다루지 않아도 되게.
            return JSONResponse({"camera_id": camera_id, "assigned": False,
                                 "calibrated": False, "tracks": [], "boxes": [],
                                 "zone_boxes": [], "zones": []})
        return JSONResponse(dict(got, assigned=True))

    @app.put("/api/overlay")
    async def set_overlay(body: OverlayIn) -> JSONResponse:
        """그림을 브로커로도 낼지(종합 현황에 보이게 할지).

        기본은 끔이다. 켜면 코어 대시보드에 이 모듈이 만든 **위험구역과 도달범위**가
        그려진다. 사람·AMR 박스는 내지 않는다 — 그것은 객체감지·카메라 메타데이터가
        이미 내고 있고, 코어가 발행자별로 합쳐 그리므로 되실으면 두 번 그려진다.
        """
        s = service.settings
        if body.publish_live is not None:
            s.publish_live = body.publish_live
        if body.zones is not None:
            s.overlay_zones = body.zones
        s.overlay_set = True
        _commit()
        log.info("라이브 오버레이: 발행 %s · 구역 %s",
                 "켬" if cfg.publish_live else "끔",
                 "포함" if cfg.overlay_zones else "제외")
        return JSONResponse({"publish_live": cfg.publish_live,
                             "zones": cfg.overlay_zones})

    # ── 라벨·항목 (계약 3장: 코드에 박지 않는다) ────────────────────

    @app.put("/api/labels")
    async def set_labels(body: LabelsIn) -> JSONResponse:
        s = service.settings
        s.amr_labels = [x.strip() for x in body.amr if x.strip()]
        s.person_labels = [x.strip() for x in body.person if x.strip()]
        _commit()
        log.info("라벨 매핑 변경: AMR %s / 사람 %s", s.amr_labels, s.person_labels)
        return JSONResponse({"amr": cfg.amr_labels, "person": cfg.person_labels})

    @app.put("/api/items")
    async def set_items(body: ItemsIn) -> JSONResponse:
        """어떤 탐지 항목으로 낼지. 비우면 그 판정은 이벤트를 만들지 않는다.

        항목 자체는 사람이 플랫폼에 먼저 만들어야 한다 — `capabilities` 는 선언일 뿐
        요구가 아니다(계약 7장).
        """
        s = service.settings
        s.risk_item = body.risk.strip()
        s.collision_item = body.collision.strip()
        _commit()
        log.info("항목 매핑 변경: 위험 %s / 충돌 %s",
                 s.risk_item or "(없음)", s.collision_item or "(없음)")
        return JSONResponse({"risk": cfg.risk_item, "collision": cfg.collision_item})

    # ── 임계값 ──────────────────────────────────────────────────────

    @app.put("/api/tuning")
    async def set_tuning(body: dict[str, float]) -> JSONResponse:
        unknown = sorted(set(body) - set(settings_module.TUNING))
        if unknown:
            raise HTTPException(status_code=400,
                                detail=f"모르는 임계값입니다: {', '.join(unknown)}")
        service.settings.set_tuning(body)
        _commit()
        log.info("임계값 변경: %s", body)
        return JSONResponse(cfg.tuning)

    # ── 보정 (기획 5.2 · 8장) ───────────────────────────────────────

    @app.put("/api/cameras/{camera_id}/calibration")
    async def set_calibration(camera_id: int, body: CalibrationIn) -> JSONResponse:
        """바닥 4점. `u,v` 는 스냅샷 위의 **정규화** 좌표, `x,y` 는 실제 미터다.

        저장 전에 실제로 풀리는지 확인한다. 한 줄에 가까운 4점이나 같은 점을 두 번 찍은
        것을 그대로 받으면, 그 뒤로 나오는 거리는 숫자이긴 하지만 뜻이 없다.
        """
        if len(body.points) != 4:
            raise HTTPException(status_code=400, detail="바닥 4점이 필요합니다")
        for p in body.points:
            if not (0.0 <= p.u <= 1.0 and 0.0 <= p.v <= 1.0):
                raise HTTPException(status_code=400,
                                    detail="이미지 좌표는 0~1 정규화여야 합니다")
        pairs = [((p.u, p.v), (p.x, p.y)) for p in body.points]
        H = geometry.homography_from_points(pairs)
        if H is None:
            raise HTTPException(
                status_code=400,
                detail="이 4점으로는 바닥 평면을 잡을 수 없습니다. 한 줄에 놓이지 않게, "
                       "가능한 한 넓게 퍼진 네 점을 고르세요.")
        error = geometry.reprojection_error(H, pairs)

        setup = _setup(camera_id)
        setup.points = [settings_module.CalibPoint(u=p.u, v=p.v, x=p.x, y=p.y)
                        for p in body.points]
        _commit()
        log.info("카메라 %d 보정 저장 (평균 오차 %.3fm)", camera_id, error)
        return JSONResponse({"camera_id": camera_id, "error_m": round(error, 3),
                             "warn": error > WARN_ERROR_M})

    @app.delete("/api/cameras/{camera_id}/calibration")
    async def clear_calibration(camera_id: int) -> JSONResponse:
        setup = _setup(camera_id)
        setup.points = []
        _commit()
        log.info("카메라 %d 보정 삭제 — 판정을 멈춥니다", camera_id)
        return JSONResponse({"camera_id": camera_id, "calibrated": False})

    @app.put("/api/cameras/{camera_id}")
    async def set_camera(camera_id: int, body: CameraIn) -> JSONResponse:
        """이 카메라를 볼지 말지, 그리고 메모.

        담당(플랫폼 할당)과 다른 층이다. 할당은 사람이 플랫폼에서 정하고, 여기 스위치는
        '할당은 그대로 두되 이 카메라는 잠시 판정하지 마라' 다 — 보정을 고치는 동안
        오알람을 내보내지 않으려면 필요하다.
        """
        setup = _setup(camera_id)
        if body.enabled is not None:
            setup.enabled = body.enabled
        if body.note is not None:
            setup.note = body.note.strip()[:500]
        _commit()
        return JSONResponse({"camera_id": camera_id, "enabled": setup.enabled,
                             "note": setup.note})

    # ── 증거 ────────────────────────────────────────────────────────

    @app.get("/api/evidence/{name}")
    async def evidence(name: str) -> FileResponse:
        path = (cfg.evidence_dir / name).resolve()
        root = cfg.evidence_dir.resolve()
        # 경로를 벗어나는 이름으로 볼륨 전체를 읽히지 않는다.
        if root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="증거를 찾을 수 없습니다")
        return FileResponse(str(path), media_type="image/jpeg")

    # ── 화면 ────────────────────────────────────────────────────────

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(WEB / "index.html"))

    @app.get("/app.js")
    async def script() -> FileResponse:
        return FileResponse(str(WEB / "app.js"), media_type="application/javascript")

    return app

"""서버 부팅 · API · 인바운드 바인딩 스모크 테스트 (SQLite + 브로커 없음).

핵심 검증: **우리 규약 밖의 임의 토픽도 바인딩만 만들면 이벤트가 되는가.**
플랫폼의 존재 이유라 여기가 깨지면 나머지는 의미가 없다.
"""

import asyncio
import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./.smoke.db"
os.environ["MQTT_HOST"] = "127.0.0.1"
os.environ["SNAPSHOT_DIR"] = "./.smoke-snap"
os.environ["RECORD_DIR"] = "./.smoke-rec"
os.environ["TZ"] = "Asia/Seoul"
# 설정 파일은 이 테스트가 직접 다룬다. 배포용 파일을 붓고 시작하면 '기본 배선이 있는 상태'
# 를 검사하는 셈이라, 바인딩이 하나도 없을 때의 동작을 확인할 수 없다.
os.environ["CONFIG_FILE"] = "./.smoke-config.json"

db = pathlib.Path("./.smoke.db")
if db.exists():
    db.unlink()
import shutil                                        # noqa: E402

shutil.rmtree("./.smoke-rec", ignore_errors=True)
pathlib.Path("./.smoke-config.json").unlink(missing_ok=True)

from httpx import ASGITransport, AsyncClient          # noqa: E402

from aivision_server.config import get_settings      # noqa: E402
from aivision_server.db import sessionmaker           # noqa: E402
from aivision_server.main import app                  # noqa: E402
from aivision_server.models import Solution           # noqa: E402

PPE_TOPIC = "E4:30:22:F3:31:AA/onvif-ej/Device/tns1:Trigger/tns1:Relay/&Relay-1"

# ONVIF 페이로드에 실어 보낼 발생 시각.
#
# 날짜를 박아 두면 안 된다. onvif.event_time() 은 카메라 시계가 틀어진 경우를 막으려고
# **1일 이상 과거인 시각을 믿지 않고 수신 시각으로 대체**하는데, 고정 날짜는 하루만 지나면
# 그 규칙에 걸려 어제까지 통과하던 테스트가 오늘 갑자기 깨진다(실제로 그렇게 깨졌다).
#
# 1시간 전으로 두는 이유는 두 조건을 동시에 만족해야 해서다.
#   · 신뢰 구간 안 (24시간보다 최근)     -> 페이로드 시각이 그대로 쓰인다
#   · 중복 억제 창(20초) 밖              -> 뒤에 오는 같은 항목 신호가 이것에 묻히지 않는다
ONVIF_TS = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  OK  " if cond else "FAIL  ") + name + (f"   << {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


async def main() -> int:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        async with app.router.lifespan_context(app):
            from aivision_server.detection.mqtt_source import MqttInboundSource
            from aivision_server.detection.registry import _handle_signal
            from aivision_server.services.binding import engine

            src = MqttInboundSource()
            src._handler = _handle_signal

            async def feed(topic: str, payload: str = "") -> None:
                """브로커를 거치지 않고 수신 경로를 직접 태운다."""
                await src._handle(topic, payload.encode())

            async def events() -> dict:
                return (await c.get("/api/events?limit=50")).json()

            # ── 기본 ────────────────────────────────────────────────────
            check("health 200", (await c.get("/api/health")).status_code == 200)
            check("탐지 항목 초기 0건", (await c.get("/api/solutions")).json() == [])
            check("바인딩 초기 0건", (await c.get("/api/bindings")).json() == [])
            check("카메라 초기 0대", (await c.get("/api/cameras")).json() == [])

            # ── 카메라: IP 만으로 등록 ──────────────────────────────────
            r = await c.post("/api/cameras", json={"ip": "192.168.10.99"})
            check("IP 만으로 등록", r.status_code == 201, r.text)
            bare = r.json()
            check("이름·위치 자동 채움",
                  bare["name"] == "192.168.10.99" and bare["location"] == "192.168.10.99",
                  str(bare))
            await c.delete(f"/api/cameras/{bare['id']}")

            r = await c.post("/api/cameras", json={
                "ip": "192.168.10.11", "name": "현장 카메라 #1", "location": "1번 게이트",
                "mac": "e4-30-22-f3-31-aa", "username": "admin", "password": "secret"})
            check("카메라 등록 201", r.status_code == 201, r.text)
            cam = r.json()
            cid = cam["id"]
            check("MAC 정규화", cam["mac"] == "E4:30:22:F3:31:AA", cam["mac"])
            # 평문으로 저장하지만 목록 응답에는 싣지 않는다. 보호라기보다, 카메라 목록을
            # 부를 때마다 자격증명이 오갈 이유가 없어서다.
            check("비밀번호 미노출", "password" not in cam and cam["has_password"] is True)
            check("중복 등록 409",
                  (await c.post("/api/cameras", json={"ip": "192.168.10.11"})).status_code == 409)

            # ── 보조(저화질) 스트림 ─────────────────────────────────────
            # 추론 모듈이 4K 를 디코딩할 이유가 없다. '없다' 와 '주 스트림과 같다' 를
            # 구분하는 것이 요점 — 같다고 보면 저화질인 줄 알고 원본을 준다.
            from aivision_server.models import Camera as _Cam
            from aivision_server.streaming.manager import rtsp_url as _rtsp

            async with sessionmaker()() as sdb:
                cam_row = await sdb.get(_Cam, cid)
                check("보조 경로가 비어 있으면 주소도 빈 값",
                      _rtsp(cam_row, sub=True) == "", _rtsp(cam_row, sub=True))

            r = await c.patch(f"/api/cameras/{cid}",
                              json={"rtsp_path_sub": "/profile3/media.smp"})
            check("보조 스트림 경로 저장",
                  r.json()["rtsp_path_sub"] == "/profile3/media.smp", r.text)

            async with sessionmaker()() as sdb:
                cam_row = await sdb.get(_Cam, cid)
                main_url = _rtsp(cam_row)
                sub_url = _rtsp(cam_row, sub=True)
            check("보조 주소가 주 주소와 다르다", bool(sub_url) and sub_url != main_url,
                  f"{main_url} / {sub_url}")
            check("보조 주소에도 자격증명이 붙는다", "admin" in sub_url, sub_url)

            # ── 화면 표시 순서 ──────────────────────────────────────────
            # 등록 순서가 곧 보고 싶은 순서인 경우는 드물다. 브라우저가 아니라 서버가
            # 들고 있어야 관제실 PC 가 여러 대여도 같게 보인다.
            second = (await c.post("/api/cameras", json={"ip": "192.168.10.12"})).json()
            ids = [x["id"] for x in (await c.get("/api/cameras")).json()]
            check("기본은 등록 순서", ids == sorted(ids), str(ids))

            r = await c.put("/api/cameras/order", json={"ids": [second["id"], cid]})
            check("순서 바꾸기 200", r.status_code == 200, r.text)
            check("바꾼 순서로 응답", [x["id"] for x in r.json()] == [second["id"], cid],
                  str([x["id"] for x in r.json()]))
            again = [x["id"] for x in (await c.get("/api/cameras")).json()]
            check("다시 조회해도 유지", again == [second["id"], cid], str(again))

            # 화면이 필터를 걸어 일부만 보내도, 빠진 카메라가 맨 앞으로 튀어나오면 안 된다
            r = await c.put("/api/cameras/order", json={"ids": [cid]})
            check("목록에 없는 카메라는 뒤로",
                  [x["id"] for x in r.json()] == [cid, second["id"]],
                  str([x["id"] for x in r.json()]))

            r = await c.put("/api/cameras/order", json={"ids": [9999]})
            check("없는 카메라 400", r.status_code == 400, r.text)
            await c.delete(f"/api/cameras/{second['id']}")

            # ── 탐지 항목 (현장에서 정하는 것이므로 시드는 없다) ─────────
            async with sessionmaker()() as sdb:
                sdb.add(Solution(code="ITEM-A", name="시험 항목 A", short_name="A",
                                 event_type="A 감지", color="#c64545", sort_order=1))
                sdb.add(Solution(code="ITEM-B", name="시험 항목 B", short_name="B",
                                 event_type="B 감지", color="#d4a017", sort_order=2))
                sdb.add(Solution(code="ITEM-C", name="시험 항목 C", short_name="C",
                                 event_type="C 감지", color="#5db8a6", sort_order=3))
                await sdb.commit()
            await engine.reload()
            check("탐지 항목 생성", len((await c.get("/api/solutions")).json()) == 3)

            # ── 바인딩 1: 페이로드 없는 토픽 = 수신 자체가 발생 ──────────
            r = await c.post("/api/bindings", json={
                "name": "장비 화재 알람", "topic_pattern": "+/fireAlarm",
                "camera_from": "topic_mac", "item_from": "fixed", "solution_code": "ITEM-A"})
            check("바인딩 생성 201", r.status_code == 201, r.text)

            await feed("E4:30:22:F3:31:AA/fireAlarm")
            page = await events()
            check("수신 자체가 발생", page["total"] == 1, str(page))
            check("이벤트 코드 형식", page["items"][0]["id"] == "EVT-00001")
            check("항목 매칭", page["items"][0]["sol"] == "ITEM-A", str(page["items"][0]))

            # ── 바인딩 2: ONVIF SimpleItem 프로파일 ─────────────────────
            await c.post("/api/bindings", json={
                "name": "ONVIF 릴레이", "topic_pattern": "+/onvif-ej/#",
                "payload_profile": "onvif", "camera_from": "topic_mac",
                "item_from": "fixed", "solution_code": "ITEM-B",
                "state_expr": "$.Data.LogicalState", "ts_expr": "$.UtcTime"})

            await feed(PPE_TOPIC, json.dumps({
                "UtcTime": ONVIF_TS,
                "Source": {"SimpleItem": [{"Name": "RelayToken", "Value": "Relay-1"}]},
                "Data": {"SimpleItem": [{"Name": "LogicalState", "Value": "active"}]}}))
            page = await events()
            check("ONVIF SimpleItem 해석", page["total"] == 2, str(page["total"]))

            await feed(PPE_TOPIC, json.dumps({"Data": {"LogicalState": "inactive"}}))
            check("해제 신호는 이벤트 아님", (await events())["total"] == 2)

            # ── 바인딩 3: 우리 규약 밖의 임의 토픽 (플랫폼의 핵심) ───────
            r = await c.post("/api/bindings", json={
                "name": "협력사 X", "topic_pattern": "vendorX/+/alarm",
                "camera_from": "payload", "camera_expr": "$.cam",
                "item_from": "payload", "item_expr": "$.kind",
                "state_expr": "$.on", "state_active": "1", "state_inactive": "0",
                "confidence_expr": "$.conf",
                "boxes_expr": "$.dets", "boxes_format": "xyxy_px"})
            check("임의 토픽 바인딩 생성", r.status_code == 201, r.text)

            await feed("vendorX/site1/alarm", json.dumps({
                "cam": cid, "kind": "ITEM-C", "on": "1", "conf": 91,
                "dets": [{"x1": 320, "y1": 180, "x2": 640, "y2": 540,
                          "imgW": 1280, "imgH": 720, "label": "person", "score": 0.9}]}))
            page = await events()
            check("우리 규약 밖 토픽도 이벤트로", page["total"] == 3, str(page["total"]))
            hits = [e for e in page["items"] if e["sol"] == "ITEM-C"]
            check("벤더 이벤트 생성", len(hits) == 1, str(page["items"]))
            ev = hits[0] if hits else {"cam": None, "confidence": None, "boxes": []}
            check("페이로드에서 카메라 해석", ev["cam"] == cid, str(ev))
            check("신뢰도 0~100 -> 0~1", abs((ev["confidence"] or 0) - 0.91) < 0.01, str(ev))
            box = ev["boxes"][0] if ev["boxes"] else {}
            check("픽셀 박스 -> 정규화",
                  box and abs(box["x1"] - 0.25) < 0.01 and abs(box["y2"] - 0.75) < 0.01, str(box))

            # 같은 카메라·같은 항목을 곧바로 다시 쏘면 중복 억제로 묻힌다
            await feed("vendorX/site1/alarm", json.dumps({"cam": cid, "kind": "ITEM-C", "on": "1"}))
            check("중복 억제", (await events())["total"] == 3)

            # ── 시험 엔드포인트 ─────────────────────────────────────────
            r = await c.post("/api/bindings/test", json={
                "topic": "vendorX/site1/alarm",
                "payload": json.dumps({"cam": cid, "kind": "ITEM-C", "on": "1"})})
            res = r.json()
            hit = [x for x in res if x["matched"]]
            check("시험: 걸리는 바인딩 식별", len(hit) == 1 and hit[0]["item"] == "ITEM-C", str(res))
            miss = [x for x in res if not x["matched"]]
            check("시험: 안 걸린 사유 제공", all(x["reason"] for x in miss), str(miss))

            r = await c.post("/api/bindings/test", json={
                "topic": "vendorX/site1/alarm",
                "payload": json.dumps({"cam": 999, "kind": "ITEM-C", "on": "1"})})
            bad = [x for x in r.json() if x["binding_name"] == "협력사 X"][0]
            check("시험: 미등록 카메라 사유", not bad["matched"] and "카메라" in bad["reason"],
                  str(bad))

            # ── 라이브 전용 바인딩은 적재하지 않는다 ────────────────────
            await c.post("/api/bindings", json={
                "name": "라이브 박스", "topic_pattern": "aivision/live/+",
                "live_only": True, "camera_from": "topic_segment", "camera_expr": "$topic[2]",
                "item_from": "fixed", "solution_code": "ITEM-A",
                "boxes_expr": "$.boxes", "boxes_format": "xyxy_norm"})
            before = (await events())["total"]
            await feed(f"aivision/live/{cid}", json.dumps({
                "boxes": [{"x1": 0.1, "y1": 0.1, "x2": 0.4, "y2": 0.6, "label": "x", "score": 0.7}]}))
            check("라이브 전용은 적재 안 함", (await events())["total"] == before)

            # ── 미등록 장치 · heartbeat ─────────────────────────────────
            await feed("AA:BB:CC:DD:EE:FF/fireAlarm")
            check("미등록 MAC 무시", (await events())["total"] == before)
            await feed("E4:30:22:F3:31:AA/heartbeat")
            check("heartbeat 무시", (await events())["total"] == before)

            # ── 바인딩 검증 ─────────────────────────────────────────────
            r = await c.post("/api/bindings", json={
                "name": "없는 항목", "topic_pattern": "z/#",
                "item_from": "fixed", "solution_code": "NOPE"})
            check("없는 탐지 항목 400", r.status_code == 400, r.text)
            r = await c.post("/api/bindings", json={
                "name": "잘못된 프로파일", "topic_pattern": "z/#",
                "payload_profile": "xml", "item_from": "fixed", "solution_code": "ITEM-A"})
            check("잘못된 프로파일 400", r.status_code == 400, r.text)

            # ── 바인딩 끄면 안 걸린다 ──────────────────────────────────
            blist = (await c.get("/api/bindings")).json()
            fire = [b for b in blist if b["name"] == "장비 화재 알람"][0]
            check("매칭 카운터 기록", fire["match_count"] >= 1, str(fire))
            await c.patch(f"/api/bindings/{fire['id']}", json={"enabled": False})
            n = (await events())["total"]
            await feed("E4:30:22:F3:31:AA/fireAlarm")
            check("비활성 바인딩은 안 걸림", (await events())["total"] == n)

            # ── 분석 모듈: 등록 -> 할당 -> 일감 수령 ─────────────────────
            r = await c.post("/api/modules", json={
                "id": "yolo-ppe", "name": "PPE 판독", "kind": "sidecar",
                "capabilities": ["ITEM-A", "ITEM-C"], "description": "시험용"})
            check("모듈 등록 201", r.status_code == 201, r.text)
            check("등록 직후는 alive", r.json()["alive"] is True, str(r.json()))

            # 같은 id 로 다시 등록해도 실패하지 않아야 한다(모듈 재시작 때마다 죽으면 곤란).
            r = await c.post("/api/modules", json={"id": "yolo-ppe", "kind": "remote"})
            check("재등록은 갱신", r.status_code == 201 and r.json()["kind"] == "remote", r.text)

            r = await c.post("/api/modules/yolo-ppe/assignments",
                             json={"camera_id": cid, "options": {"min_conf": 0.5}})
            check("카메라 할당 201", r.status_code == 201, r.text)
            r = await c.post("/api/modules/yolo-ppe/assignments", json={"camera_id": cid})
            check("중복 할당 409", r.status_code == 409, r.text)
            r = await c.post("/api/modules/yolo-ppe/assignments", json={"camera_id": 9999})
            check("없는 카메라 할당 400", r.status_code == 400, r.text)

            work = (await c.get("/api/modules/yolo-ppe/work")).json()
            check("일감 1건", len(work["items"]) == 1, str(work))
            item = work["items"][0]
            check("일감에 카메라·옵션", item["camera_id"] == cid
                  and item["options"] == {"min_conf": 0.5}, str(item))
            check("일감에 카메라 자격증명 없음",
                  "ip" not in item and "username" not in item and "password" not in item,
                  str(item))

            r = await c.post("/api/modules/yolo-ppe/heartbeat", json={"fps": 12.5})
            check("heartbeat", r.status_code == 200 and r.json()["last_status"]["fps"] == 12.5,
                  r.text)
            check("없는 모듈 heartbeat 404",
                  (await c.post("/api/modules/nope/heartbeat", json={})).status_code == 404)

            # ── HTTP 인바운드: MQTT 와 같은 바인딩 층을 지나는가 ──────────
            r = await c.post("/api/bindings", json={
                "name": "HTTP 모듈", "transport": "http", "topic_pattern": "aivision/detect/#",
                "camera_from": "payload", "camera_expr": "$.camera_id",
                "item_from": "payload", "item_expr": "$.item",
                "confidence_expr": "$.confidence",
                "boxes_expr": "$.boxes", "boxes_format": "xyxy_norm"})
            check("HTTP 바인딩 생성", r.status_code == 201, r.text)

            before = (await events())["total"]
            r = await c.post("/api/ingest", json={
                "topic": "aivision/detect/1/yolo-ppe",
                "payload": {"camera_id": cid, "item": "ITEM-B", "confidence": 0.77,
                            "boxes": [{"x1": 0.2, "y1": 0.3, "x2": 0.5, "y2": 0.8,
                                       "label": "helmet", "score": 0.77}]}})
            res = r.json()
            check("HTTP 인제스트 수락", r.status_code == 200 and res["matched"] == 1, r.text)
            page = await events()
            check("HTTP 로도 이벤트 생성", page["total"] == before + 1, str(page["total"]))
            hb = [e for e in page["items"] if e["sol"] == "ITEM-B"]
            check("HTTP 이벤트 소스 표기", hb and hb[0]["source"] == "http", str(hb[:1]))
            check("HTTP 이벤트 박스", hb and len(hb[0]["boxes"]) == 1, str(hb[:1]))

            # 규칙이 없는 토픽은 거절이 아니라 '보관 후 안내' 여야 한다
            r = await c.post("/api/ingest", json={"topic": "unknown/thing", "payload": {"a": 1}})
            check("규칙 없어도 200 + 안내",
                  r.status_code == 200 and r.json()["matched"] == 0 and r.json()["detail"],
                  r.text)

            # 판정 주체는 바인딩 이름이 아니라 페이로드에서 뽑은 값이어야 한다.
            # (바인딩 이름을 넣으면 '어느 규칙이 걸렸나' 와 '무엇이 판정했나' 가 뒤섞인다)
            async with sessionmaker()() as sdb:
                # 새 항목을 쓴다. 기존 항목은 앞선 테스트에서 이미 이벤트가 나서
                # 중복 억제 창(20초)에 걸린다.
                sdb.add(Solution(code="ITEM-D", name="시험 항목 D", short_name="D",
                                 event_type="시험 D"))
                await sdb.commit()
            r = await c.post("/api/bindings", json={
                "name": "판정주체 확인", "transport": "http", "topic_pattern": "probe/module",
                "camera_from": "fixed", "camera_id": cid,
                "item_from": "fixed", "solution_code": "ITEM-D",
                "module_expr": "$.module_id", "state_expr": ""})
            check("판정 주체 표현식을 받는 바인딩 생성", r.status_code == 201, r.text)
            mod_binding = r.json()["id"]
            await c.post("/api/ingest", json={
                "topic": "probe/module", "payload": {"module_id": "yolo-server"}})
            evs = await events()
            picked = [e for e in evs["items"] if e["module"] == "yolo-server"]
            check("이벤트에 판정 주체가 남는다", len(picked) == 1,
                  str([(e["id"], e.get("module")) for e in evs["items"]]))
            await c.delete(f"/api/bindings/{mod_binding}")

            # MQTT 바인딩은 HTTP 로 들어온 것에 걸리지 않아야 한다(전송별 격리)
            r = await c.post("/api/ingest", json={
                "topic": "E4:30:22:F3:31:AA/fireAlarm", "payload": {}})
            check("전송 격리(MQTT 규칙은 HTTP 에 안 걸림)", r.json()["matched"] == 0, r.text)

            # ── 아웃바운드: outbox 가 실제로 동작하는가 ─────────────────
            # 여기서는 브로커가 없다(MQTT_HOST=127.0.0.1). 즉 발송은 반드시 실패한다.
            # 그 상황에서 이벤트를 잃지 않고 재시도 대기로 남는지가 이 기능의 값이다.
            from aivision_server.services import outbound as ob

            fields = (await c.get("/api/outbound/fields")).json()
            check("템플릿 필드 목록 제공", "event.code" in fields and "boxes_json" in fields,
                  str(fields))

            # 값 안의 따옴표 하나로 모든 발송이 깨진 JSON 이 되어서는 안 된다.
            from aivision_server.services import template as _tpl

            ctx = {"camera.name": '1층 "정문" 카메라', "event.code": "EVT-1"}
            out = _tpl.render_json('{"cam":"{camera.name}","e":"{event.code}"}', ctx)
            import json as _json

            try:
                parsed = _json.loads(out)
            except ValueError:
                parsed = None
            check("값에 따옴표가 있어도 페이로드가 JSON 으로 유지됨",
                  parsed is not None and parsed["cam"] == '1층 "정문" 카메라', out)

            r = await c.post("/api/outbound/targets", json={
                "name": "상위 관제", "kind": "mqtt",
                "config": {"topic_template": "plant1/alarm/{camera.id}", "qos": 0},
                "payload_template": '{"e":"{event.code}","item":"{item.code}"}',
                "max_attempts": 3, "retry_backoff_sec": 1.0})
            check("아웃바운드 대상 생성 201", r.status_code == 201, r.text)
            target_id = r.json()["id"]

            r = await c.post("/api/outbound/targets", json={"name": "웹훅", "kind": "webhook"})
            check("미지원 종류 400", r.status_code == 400, r.text)

            # 필터가 걸리는 대상도 하나 만든다(이 이벤트와 맞지 않는 항목)
            await c.post("/api/outbound/targets", json={
                "name": "다른 항목만", "kind": "mqtt", "solution_codes": ["ITEM-B"],
                "config": {"topic_template": "x/{event.code}"}})

            # 적재 대상 이벤트를 **명시적으로** 고른다. '최근 것' 을 쓰면 앞선 테스트가
            # 남긴 항목에 따라 필터 결과가 달라져 테스트가 흔들린다.
            evs = await events()
            picked = [e for e in evs["items"] if e["sol"] == "ITEM-A"]
            check("적재용 이벤트 확보", len(picked) == 1, str([e["sol"] for e in evs["items"]]))
            target_event = picked[0]["id"]

            async with sessionmaker()() as odb:
                from sqlalchemy import select as _sel

                from aivision_server.models import Event as _Ev

                ev = (await odb.execute(_sel(_Ev).where(_Ev.code == target_event)
                                        )).scalars().unique().first()
                made = await ob.enqueue_for_event(odb, ev)
            # 대상 둘 중 하나는 ITEM-B 만 받으므로 ITEM-A 이벤트에는 걸리지 않아야 한다.
            check("조건에 맞는 대상만 적재", made == 1, f"{made}건 적재됨")

            dels = (await c.get("/api/outbound/deliveries")).json()
            check("outbox 에 대기로 남음", dels and dels[0]["status"] == "pending", str(dels[:1]))
            check("토픽 템플릿 치환",
                  dels and dels[0]["topic"] == f"plant1/alarm/{cid}", str(dels[:1]))
            check("페이로드 템플릿 치환",
                  dels and target_event in (dels[0].get("topic", "") + str(dels[0])),
                  str(dels[:1]))

            # 발송 시도 -> 브로커가 없으니 실패하고 재시도 대기로 남아야 한다
            r = await c.post("/api/outbound/drain")
            check("발송 시도됨", r.json()["handled"] == 1, r.text)
            dels = (await c.get("/api/outbound/deliveries")).json()
            first = dels[0] if dels else {}
            check("실패해도 이벤트를 잃지 않음",
                  first.get("status") == "failed" and first.get("attempt") == 1, str(first))
            check("실패 사유가 남음", bool(first.get("error")), str(first))
            check("재시도 시각이 잡힘", bool(first.get("next_attempt_at")), str(first))

            # 최대 시도를 넘기면 만료로 끝난다(무한 재시도로 쌓이지 않는다)
            for _ in range(4):
                async with sessionmaker()() as odb:
                    from sqlalchemy import update as _upd

                    from aivision_server.models import OutboundDelivery as _D

                    await odb.execute(_upd(_D).values(next_attempt_at=None))
                    await odb.commit()
                # next_attempt_at 을 지금으로 되돌려 즉시 재시도되게 한다
                async with sessionmaker()() as odb:
                    from datetime import datetime as _dt, timezone as _tz

                    from sqlalchemy import update as _upd

                    from aivision_server.models import OutboundDelivery as _D

                    await odb.execute(_upd(_D).where(_D.status == "failed")
                                      .values(next_attempt_at=_dt.now(_tz.utc)))
                    await odb.commit()
                await c.post("/api/outbound/drain")
            dels = (await c.get("/api/outbound/deliveries")).json()
            check("최대 시도 초과 시 만료",
                  dels and dels[0]["status"] == "expired", str(dels[:1]))

            # 대상을 지우면 대기분도 함께 정리된다(FK CASCADE)
            check("대상 삭제 204",
                  (await c.delete(f"/api/outbound/targets/{target_id}")).status_code == 204)
            dels = (await c.get("/api/outbound/deliveries", params={"target_id": target_id})).json()
            check("대상 삭제 시 이력도 정리", dels == [], str(dels[:1]))

            # ── 통계 · CSV · 원문 로그 ──────────────────────────────────
            for bucket in ("hourly", "daily", "weekly", "monthly"):
                r = await c.get(f"/api/stats?bucket={bucket}")
                check(f"통계 {bucket}", r.status_code == 200 and len(r.json()["series"]) > 0, r.text)
            st = (await c.get("/api/stats?bucket=daily")).json()
            codes = {row["code"] for row in st["by_solution"]}
            check("통계 항목별 집계",
                  codes == {"ITEM-A", "ITEM-B", "ITEM-C", "ITEM-D"},
                  str(st["by_solution"]))

            s = (await c.get("/api/events/summary")).json()
            check("요약 KPI", s["cameras_total"] == 1, str(s))

            r = await c.get("/api/events/export.csv")
            check("CSV BOM + 한글 헤더", r.text.startswith("﻿") and "이벤트 ID" in r.text,
                  repr(r.text[:40]))

            # ── 녹화 용량 상한 ─────────────────────────────────────────
            # 미디어 서버는 시간 기반 회전까지만 할 수 있다. 용량 상한은 코어가 지키고,
            # 그 규칙이 '오래된 것부터, 쓰고 있는 것은 빼고' 인지가 여기서 확인된다.
            import time as _time

            from aivision_server.services import recording as rec

            root = get_settings().record_path / "cam" / "1"
            root.mkdir(parents=True, exist_ok=True)
            for name in ("old.mp4", "mid.mp4", "new.mp4"):
                (root / name).write_bytes(b"x" * 400_000)
            old_ts = _time.time() - 3600
            os.utime(root / "old.mp4", (old_ts, old_ts))
            os.utime(root / "mid.mp4", (old_ts + 600, old_ts + 600))
            os.utime(root / "new.mp4", (old_ts + 1200, old_ts + 1200))

            u = rec.usage()
            check("녹화 용량 집계", u["files"] == 3 and u["bytes"] == 1_200_000, str(u))

            r = await c.post("/api/system/record/purge")
            check("상한 없으면 정리 거부 400", r.status_code == 400, r.text)

            out = await rec.enforce_quota(max_gb=0)
            check("상한 0 은 무제한", out["deleted"] == 0, str(out))

            # 1.2MB 를 0.8MB 상한으로 누른다 -> 가장 오래된 하나가 지워져야 한다
            out = await rec.enforce_quota(max_gb=800_000 / (1024 ** 3))
            names = sorted(p.name for p in root.iterdir())
            check("상한 초과 시 오래된 것부터 삭제",
                  out["deleted"] == 1 and names == ["mid.mp4", "new.mp4"], f"{out} {names}")

            # 아무리 좁혀도 마지막 한 건(쓰는 중일 수 있다)은 남긴다
            out = await rec.enforce_quota(max_gb=1 / (1024 ** 3))
            names = sorted(p.name for p in root.iterdir())
            check("쓰는 중일 수 있는 최신 세그먼트는 남긴다", names == ["new.mp4"], f"{out} {names}")

            r = await c.put("/api/settings", json={"record_max_gb": 5})
            check("용량 상한 저장", r.json()["record_max_gb"] == 5, r.text)
            sysinfo = (await c.get("/api/system")).json()
            check("시스템 상태에 녹화 사용량 노출",
                  sysinfo.get("record", {}).get("limit_gb") == 5, str(sysinfo.get("record")))
            await c.put("/api/settings", json={"record_max_gb": 0})

            # ── 완충장치: 같은 메시지가 쏟아질 때 ───────────────────────
            # 현장 시나리오 그대로다. 카메라 엣지가 토픽 "invasion" 에 고정 문구
            # "someone invasion" 을 발행하고, 그것이 초당 여러 번 쏟아진다.
            from aivision_server.services import throttle

            async with sessionmaker()() as sdb:
                sdb.add(Solution(code="INVASION", name="invasion", short_name="침입",
                                 event_type="침입 감지", color="#c64545", sort_order=9))
                await sdb.commit()
            await engine.reload()

            r = await c.post("/api/bindings", json={
                "name": "invasion", "topic_pattern": "invasion",
                "camera_from": "fixed", "camera_id": cid,
                "item_from": "fixed", "solution_code": "INVASION",
                "state_expr": ""})          # 수신 자체가 발생
            check("invasion 바인딩 생성", r.status_code == 201, r.text)
            invasion_id = r.json()["id"]

            before = (await events())["total"]
            for _ in range(50):                      # 같은 메시지 50연발
                await feed("invasion", "someone invasion")
            after = (await events())["total"]
            check("연발해도 이벤트는 하나", after == before + 1, f"{before} -> {after}")
            st = throttle.status()
            check("나머지는 문 앞에서 버려진다", st["dropped"] >= 49, str(st))

            page = await events()
            hit = [e for e in page["items"] if e["sol"] == "INVASION"]
            check("invasion 이벤트 생성", len(hit) == 1, str([e["sol"] for e in page["items"]]))
            check("평문 페이로드도 받는다", hit[0]["type"] == "침입 감지", str(hit[0]))

            # 빈 페이로드 연발 (한화비전이 이따금 뿜는 것). 첫 건은 통과하지만
            # 중복 억제가 받아 이벤트로는 안 쌓인다. 나머지는 완충장치가 버린다.
            dropped_before = throttle.status()["dropped"]
            for _ in range(30):
                await feed("invasion", "")
            check("빈 메시지 연발도 막힌다",
                  throttle.status()["dropped"] - dropped_before >= 29,
                  str(throttle.status()))

            # 내용이 다르면 막지 않는다 — 라이브 박스처럼 매번 달라지는 것은 통과해야 한다
            passed_before = throttle.status()["passed"]
            for i in range(5):
                await feed("probe/varies", json.dumps({"n": i}))
            check("내용이 다르면 통과시킨다",
                  throttle.status()["passed"] - passed_before == 5, str(throttle.status()))

            # 내용 조건으로 아예 걸러 낼 수도 있다. 완충장치는 '빈도' 를 막고,
            # 내용 조건은 '무엇을' 막는다 — 빈 메시지를 이벤트로 만들지 않으려면 이쪽이다.
            await c.patch(f"/api/bindings/{invasion_id}",
                          json={"payload_filter": {"$._raw": "someone invasion"}})

            async def invasion_match(payload: str) -> bool:
                res = (await c.post("/api/bindings/test",
                                    json={"topic": "invasion", "payload": payload})).json()
                hit = [x for x in res if x["binding_id"] == invasion_id]
                return bool(hit) and hit[0]["matched"]

            check("내용 조건: 빈 메시지는 안 걸린다", not await invasion_match(""))
            check("내용 조건: 다른 문구도 안 걸린다", not await invasion_match("noise"))
            check("내용 조건: 정한 문구는 걸린다", await invasion_match("someone invasion"))
            await c.patch(f"/api/bindings/{invasion_id}", json={"payload_filter": None})

            # 끌 수 있어야 한다
            r = await c.put("/api/settings", json={"inbound_min_interval_sec": 0})
            check("완충 간격 저장", r.json()["inbound_min_interval_sec"] == 0, r.text)
            await throttle.reload()
            passed_before = throttle.status()["passed"]
            for _ in range(4):
                await feed("invasion", "someone invasion")
            check("0 이면 전부 통과",
                  throttle.status()["passed"] - passed_before == 4, str(throttle.status()))
            await c.put("/api/settings", json={"inbound_min_interval_sec": 1})
            await throttle.reload()

            # ── 원문 적재 정책 ─────────────────────────────────────────
            # 기본은 남기지 않는다. 라이브 박스처럼 초당 여러 번 들어오는 트래픽을
            # 기본으로 적재하면 하루 수십만 줄이 쌓인다. 실시간 발견은 'MQTT 로그'
            # 화면이 브로커에 직결 구독해 처리하므로 DB 를 꺼도 막히지 않는다.
            logs = (await c.get("/api/mqtt-log")).json()
            check("기본값은 원문을 남기지 않음", logs == [], str(len(logs)))

            settings = (await c.get("/api/settings")).json()
            check("기본 적재 모드 off", settings["mqtt_log_mode"] == "off", str(settings))
            r = await c.put("/api/settings", json={"mqtt_log_mode": "쌓아"})
            check("알 수 없는 적재 모드 400", r.status_code == 400, r.text)

            await c.put("/api/settings", json={"mqtt_log_mode": "all"})
            await feed("vendorZ/keep/this", json.dumps({"a": 1}))
            await feed("E4:30:22:F3:31:AA/onvif-ej/Device/tns1:Trigger/tns1:Relay/&Relay-1",
                       json.dumps({"Data": {"SimpleItem": []}}))
            logs = (await c.get("/api/mqtt-log")).json()
            check("all 로 켜면 남는다", any(x["topic"] == "vendorZ/keep/this" for x in logs),
                  str([x["topic"] for x in logs]))
            check("특수문자 토픽 보존", any("&Relay-1" in x["topic"] for x in logs))
            check("바인딩에 안 걸린 원문도 보관", any(not x["matched"] for x in logs), str(len(logs)))

            # 특정 채널만 지정하면 그것이 모드보다 우선한다
            await c.post("/api/system/mqtt-log/purge", json={})
            await c.put("/api/settings", json={"mqtt_log_topics": "vendorZ/#"})
            await feed("vendorZ/only/me", json.dumps({"a": 2}))
            await feed("someone/else", json.dumps({"a": 3}))
            topics = {x["topic"] for x in (await c.get("/api/mqtt-log")).json()}
            check("지정 채널만 남는다",
                  "vendorZ/only/me" in topics and "someone/else" not in topics, str(topics))

            await c.put("/api/settings", json={"mqtt_log_mode": "off", "mqtt_log_topics": ""})
            before = len((await c.get("/api/mqtt-log")).json())
            await feed("after/off", json.dumps({"a": 4}))
            after = len((await c.get("/api/mqtt-log")).json())
            check("off 로 되돌리면 다시 안 남는다", before == after, f"{before} -> {after}")

            r = await c.get("/api/system")
            check("시스템 상태", r.status_code == 200 and "detection_sources" in r.json())

            # ── 정리 ────────────────────────────────────────────────────
            check("카메라 삭제 204",
                  (await c.delete(f"/api/cameras/{cid}")).status_code == 204)
            check("삭제 시 이벤트 캐스케이드", (await events())["total"] == 0)

    print()
    if fails:
        print(f"실패 {len(fails)}건: {fails}")
        return 1
    print("모든 스모크 테스트 통과")
    return 0


sys.exit(asyncio.run(main()))

"""바인딩 엔진 — 인바운드 메시지를 DetectionSignal 로 옮긴다.

바인딩 테이블(InboundBinding)을 메모리에 캐시해 두고, 들어온 메시지마다
  1. topic_pattern 과 payload_filter 로 걸리는 바인딩을 고르고
  2. 그 바인딩의 extract 설정대로 카메라·항목·상태·박스를 뽑아
  3. DetectionSignal 을 만든다.

여기서 나온 신호는 소스가 무엇이든(카메라 엣지·서버 YOLO·협력사 모듈) 뒤가 같아진다.

캐시를 쓰는 이유: 메시지마다 DB 를 때리면 초당 수십 건에서 바로 무너진다.
바인딩이 바뀌면 `reload()` 로 갈아 끼운다 — 어드민 저장 직후 호출된다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import sessionmaker
from ..detection.base import Box, DetectionSignal
from ..models import Camera, InboundBinding, Solution
from ..mqtt import mapping

log = logging.getLogger(__name__)


@dataclass(slots=True)
class BindingResult:
    """바인딩 적용 결과. 화면의 '시험' 기능이 실패 사유까지 보여 줄 수 있어야 한다."""

    binding_id: int
    binding_name: str
    matched: bool
    signal: DetectionSignal | None = None
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _Cached:
    """DB 행을 그대로 들고 있으면 세션이 닫힌 뒤 접근이 터진다. 값만 복사해 둔다."""

    id: int
    name: str
    transport: str
    payload_profile: str
    priority: int
    live_only: bool
    topic_pattern: str
    payload_filter: dict | None
    camera_from: str
    camera_expr: str
    camera_id: int | None
    item_from: str
    item_expr: str
    solution_code: str | None
    state_expr: str
    state_active: str
    state_inactive: str
    confidence_expr: str
    ts_expr: str
    boxes_expr: str
    boxes_format: str


class BindingEngine:
    def __init__(self) -> None:
        self._bindings: list[_Cached] = []
        self._cameras_by_mac: dict[str, int] = {}
        self._camera_ids: set[int] = set()
        self._solution_codes: set[str] = set()

    # ------------------------------------------------------------- 캐시

    async def reload(self) -> None:
        async with sessionmaker()() as session:
            rows = (await session.execute(
                select(InboundBinding)
                .where(InboundBinding.enabled.is_(True))
                .order_by(InboundBinding.priority, InboundBinding.id)
            )).scalars().all()
            self._bindings = [_Cached(
                id=b.id, name=b.name, transport=b.transport,
                payload_profile=b.payload_profile, priority=b.priority, live_only=b.live_only,
                topic_pattern=b.topic_pattern, payload_filter=b.payload_filter,
                camera_from=b.camera_from, camera_expr=b.camera_expr, camera_id=b.camera_id,
                item_from=b.item_from, item_expr=b.item_expr, solution_code=b.solution_code,
                state_expr=b.state_expr, state_active=b.state_active,
                state_inactive=b.state_inactive,
                confidence_expr=b.confidence_expr, ts_expr=b.ts_expr,
                boxes_expr=b.boxes_expr, boxes_format=b.boxes_format,
            ) for b in rows]

            cams = (await session.execute(select(Camera))).scalars().unique().all()
            self._cameras_by_mac = {c.mac.upper(): c.id for c in cams if c.mac}
            self._camera_ids = {c.id for c in cams}
            self._solution_codes = {
                c for c in (await session.execute(select(Solution.code))).scalars()}

        log.info("바인딩 캐시: 규칙 %d건 · 카메라 %d대 · 탐지항목 %d종",
                 len(self._bindings), len(self._camera_ids), len(self._solution_codes))

    @property
    def count(self) -> int:
        return len(self._bindings)

    @property
    def camera_count(self) -> int:
        return len(self._camera_ids)

    def camera_by_mac(self, mac: str) -> int | None:
        return self._cameras_by_mac.get((mac or "").upper())

    # ------------------------------------------------------------- 적용

    def apply(self, topic: str, payload: dict[str, Any], *,
              transport: str = "mqtt") -> list[DetectionSignal]:
        """걸리는 바인딩을 전부 적용해 신호 목록을 만든다(0개일 수 있다)."""
        out: list[DetectionSignal] = []
        for b in self._bindings:
            if b.transport != transport:
                continue
            result = self._apply_one(b, topic, payload)
            if result.matched and result.signal is not None:
                out.append(result.signal)
        return out

    def explain(self, topic: str, payload: dict[str, Any], *,
                transport: str = "mqtt") -> list[BindingResult]:
        """모든 바인딩에 대해 걸렸는지/왜 안 걸렸는지를 돌려준다(어드민 '시험' 용)."""
        return [self._apply_one(b, topic, payload)
                for b in self._bindings if b.transport == transport]

    def _apply_one(self, b: _Cached, topic: str, payload: dict[str, Any]) -> BindingResult:
        res = lambda ok, reason="", sig=None, **d: BindingResult(  # noqa: E731
            b.id, b.name, ok, sig, reason, d)

        if not mapping.topic_matches(b.topic_pattern, topic):
            return res(False, "토픽 불일치")

        flat = mapping.preprocess(payload, b.payload_profile)
        if not mapping.matches_filter(b.payload_filter, topic=topic, payload=flat,
                                      profile=b.payload_profile):
            return res(False, "페이로드 조건 불일치")

        camera_id, why = self._resolve_camera(b, topic, flat)
        if camera_id is None:
            return res(False, why)

        solution_code, why = self._resolve_item(b, topic, flat)
        if solution_code is None:
            return res(False, why)

        state, why = self._resolve_state(b, topic, flat)
        if state is None:
            return res(False, why)

        ts = mapping.to_time(
            mapping.evaluate(b.ts_expr, topic=topic, payload=flat, profile=b.payload_profile)
            if b.ts_expr else None)
        confidence = mapping.to_float(
            mapping.evaluate(b.confidence_expr, topic=topic, payload=flat,
                             profile=b.payload_profile)) if b.confidence_expr else None
        boxes: list[Box] = []
        if b.boxes_expr:
            raw = mapping.evaluate(b.boxes_expr, topic=topic, payload=flat,
                                   profile=b.payload_profile)
            boxes = [Box(**d) for d in mapping.to_boxes(raw, b.boxes_format)]

        signal = DetectionSignal(
            camera_id=camera_id,
            solution_code=solution_code,
            state=state,
            ts=ts,
            source="mqtt" if b.transport == "mqtt" else b.transport,
            module_id=b.name,
            binding_id=b.id,
            live_only=b.live_only,
            confidence=confidence,
            boxes=boxes,
            raw_topic=topic,
            raw_payload=payload or None,
        )
        return res(True, sig=signal, camera_id=camera_id, item=solution_code,
                   state=state, boxes=len(boxes))

    # ------------------------------------------------------------- 해석기

    def _resolve_camera(self, b: _Cached, topic: str,
                        flat: dict[str, Any]) -> tuple[int | None, str]:
        if b.camera_from == mapping.CAMERA_FROM_FIXED:
            if b.camera_id in self._camera_ids:
                return b.camera_id, ""
            return None, f"지정 카메라 #{b.camera_id} 없음"

        if b.camera_from == mapping.CAMERA_FROM_MAC:
            mac, _ = self._mac_of(topic)
            if not mac:
                return None, "토픽에 MAC 이 없음"
            cid = self._cameras_by_mac.get(mac)
            return (cid, "") if cid else (None, f"미등록 MAC {mac}")

        value = mapping.evaluate(b.camera_expr, topic=topic, payload=flat,
                                 profile=b.payload_profile)
        if value is None:
            return None, f"카메라 표현식 결과 없음 ({b.camera_expr})"
        # 숫자면 카메라 id, 아니면 MAC 으로 본다.
        try:
            cid = int(str(value).strip())
        except ValueError:
            cid = self._cameras_by_mac.get(str(value).strip().upper().replace("-", ":"))
            return (cid, "") if cid else (None, f"카메라를 찾을 수 없음 ({value})")
        return (cid, "") if cid in self._camera_ids else (None, f"미등록 카메라 #{cid}")

    @staticmethod
    def _mac_of(topic: str) -> tuple[str, str]:
        from ..mqtt import onvif

        return onvif.split_topic(topic)

    def _resolve_item(self, b: _Cached, topic: str,
                      flat: dict[str, Any]) -> tuple[str | None, str]:
        if b.item_from == mapping.ITEM_FROM_FIXED:
            code = b.solution_code or b.item_expr
            if code in self._solution_codes:
                return code, ""
            return None, f"탐지 항목 '{code}' 이(가) 등록되어 있지 않음"

        value = mapping.evaluate(b.item_expr, topic=topic, payload=flat,
                                 profile=b.payload_profile)
        if value is None:
            return None, f"항목 표현식 결과 없음 ({b.item_expr})"
        code = str(value).strip()
        if code in self._solution_codes:
            return code, ""
        return None, f"탐지 항목 '{code}' 이(가) 등록되어 있지 않음"

    def _resolve_state(self, b: _Cached, topic: str,
                       flat: dict[str, Any]) -> tuple[str | None, str]:
        if not b.state_expr:
            return "active", ""          # 수신 자체가 발생
        from ..mqtt import onvif

        value = mapping.evaluate(b.state_expr, topic=topic, payload=flat,
                                 profile=b.payload_profile)
        decided = onvif.truthy(value, b.state_active, b.state_inactive)
        if decided is None:
            return None, f"상태 판정 불가 ({b.state_expr} = {value!r})"
        return ("active" if decided else "inactive"), ""

    # ------------------------------------------------------------- 통계

    async def note_matches(self, session: AsyncSession, binding_ids: list[int]) -> None:
        """어느 바인딩이 실제로 걸리고 있는지 화면에서 볼 수 있게 카운터를 올린다."""
        if not binding_ids:
            return
        await session.execute(
            update(InboundBinding)
            .where(InboundBinding.id.in_(set(binding_ids)))
            .values(last_matched_at=datetime.now(timezone.utc),
                    match_count=InboundBinding.match_count + 1))
        await session.commit()


engine = BindingEngine()

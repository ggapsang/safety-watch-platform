"""인바운드 매핑 — 어떤 모양의 메시지든 내부 정규형으로 옮긴다.

플랫폼의 핵심이다. 밖에서 오는 메시지 모양은 우리가 통제할 수 없다.
한화비전은 MQTT 본문에 ONVIF 양식을 그대로 싣고, 협력사 모듈은 각자 마음대로 보낸다.
그러므로 `DetectionSignal` 은 **코어 안의 정규형**이지 남에게 강요하는 전선 포맷이 아니다.

대신 바인딩이 선언적 매핑을 들고 그 모양을 정규형으로 옮긴다.
어드민이 토픽만 알면 코드 없이 새 소스를 받아들일 수 있어야 한다 — 그게 어댑터의 실체다.

표현식 문법 (일부러 아주 좁게 잡았다. 어드민 화면에서 설명할 수 있어야 한다)
    $.a.b.c      페이로드에서 꺼낸다. 프로파일 전처리가 끝난 평면 dict 기준.
    $topic[2]    토픽을 '/' 로 자른 뒤 n 번째 조각 (0 부터)
    $mac         토픽 앞머리의 MAC 주소 (대문자 콜론 표기로 정규화)
    $topic       토픽 전체
    그 외         리터럴 문자열
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from . import onvif

log = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────── 상수

PROFILE_RAW = "raw"
PROFILE_ONVIF = "onvif"

CAMERA_FROM_MAC = "topic_mac"
CAMERA_FROM_SEGMENT = "topic_segment"
CAMERA_FROM_PAYLOAD = "payload"
CAMERA_FROM_FIXED = "fixed"

ITEM_FROM_FIXED = "fixed"
ITEM_FROM_PAYLOAD = "payload"

BOX_XYXY_NORM = "xyxy_norm"      # 0~1 정규화. 우리 권장 형식.
BOX_XYXY_PX = "xyxy_px"
BOX_XYWH_PX = "xywh_px"
BOX_CXCYWH_NORM = "cxcywh_norm"

_SEGMENT_RE = re.compile(r"^\$topic\[(\d+)\]$")


# ────────────────────────────────────────────────────────────── 토픽 매칭

def topic_matches(pattern: str, topic: str) -> bool:
    """MQTT 와일드카드 매칭. '+' 는 한 조각, '#' 는 나머지 전부.

    브로커가 이미 걸러 준 것을 다시 확인하는 이유: 서버는 '#' 하나로 전부 구독하고
    바인딩별 매칭은 여기서 한다. 그래야 바인딩을 고쳐도 재구독이 필요 없다.
    """
    if not pattern:
        return False
    if pattern == "#":
        return True
    pat, top = pattern.split("/"), topic.split("/")
    for i, seg in enumerate(pat):
        if seg == "#":
            return True
        if i >= len(top):
            return False
        if seg != "+" and seg != top[i]:
            return False
    return len(pat) == len(top)


# ────────────────────────────────────────────────────────────── 전처리

def preprocess(payload: dict[str, Any], profile: str) -> dict[str, Any]:
    """페이로드를 평면 dict 로 만든다.

    onvif 프로파일은 Source/Data 의 SimpleItem 배열을 'Data.State' 같은 평면 키로 편다.
    raw 는 중첩 그대로 두고 조회할 때 점 표기로 내려간다.
    """
    if profile == PROFILE_ONVIF:
        return onvif.normalize(payload)
    return payload


def _dig(data: Any, path: str) -> Any:
    """점 표기로 중첩 dict/list 를 내려간다. 'a.b.0.c' 형태를 받는다."""
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
                continue
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return None
        return None
    return cur


# ────────────────────────────────────────────────────────────── 표현식

def evaluate(expr: str, *, topic: str, payload: dict[str, Any],
             profile: str = PROFILE_RAW) -> Any:
    """표현식 하나를 푼다. 문법은 모듈 docstring 참조."""
    if not expr:
        return None
    expr = expr.strip()

    if expr == "$topic":
        return topic
    if expr == "$mac":
        mac, _ = onvif.split_topic(topic)
        return mac or None

    m = _SEGMENT_RE.match(expr)
    if m:
        parts = topic.split("/")
        idx = int(m.group(1))
        return parts[idx] if idx < len(parts) else None

    if expr.startswith("$."):
        path = expr[2:]
        # onvif 프로파일은 이미 평면이라 'Data.State' 가 통째로 키다. 먼저 통짜로 찾고,
        # 없으면 점 표기로 내려간다. 두 표기를 모두 받아 주기 위한 것이다.
        if path in payload:
            return payload[path]
        found = _dig(payload, path)
        if found is not None:
            return found
        # 마지막 조각만으로도 찾아 준다(ONVIF 는 섹션 이름이 흔들린다)
        return onvif.lookup(payload, path) if profile == PROFILE_ONVIF else None

    return expr          # 리터럴


def matches_filter(payload_filter: dict[str, Any] | None, *, topic: str,
                   payload: dict[str, Any], profile: str) -> bool:
    """추가 조건. {"$.Data.Type": "fire"} 형태이며 전부 만족해야 한다."""
    if not payload_filter:
        return True
    for expr, expected in payload_filter.items():
        actual = evaluate(str(expr), topic=topic, payload=payload, profile=profile)
        if str(actual).strip().lower() != str(expected).strip().lower():
            return False
    return True


# ────────────────────────────────────────────────────────────── 값 변환

def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    # 0~100 정수로 보내는 장비가 흔하다(한화 엣지 앱의 conf 가 그렇다).
    return v / 100.0 if v > 1.0 else v


def to_time(value: Any) -> datetime:
    """페이로드 시각을 신뢰할 수 있으면 쓰고, 아니면 수신 시각."""
    return onvif.event_time({"UtcTime": value} if value else {})


def to_boxes(value: Any, fmt: str, *, frame_w: float = 0, frame_h: float = 0) -> list[dict]:
    """어떤 좌표 형식이 오든 0~1 정규화 xyxy 로 통일한다.

    정규화로 통일하는 이유: 카메라 해상도가 섞이고, 엣지 추론은 640 입력 좌표계로 뱉고,
    화면은 CSS 픽셀 위에 그린다. 픽셀로 저장하면 변환 지점이 세 군데로 흩어진다.
    """
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            box = _one_box(item, fmt, frame_w, frame_h)
        except (TypeError, ValueError):
            continue
        if box is None:
            continue
        x1, y1, x2, y2 = box
        if x2 < x1:
            x1, x2 = x2, x1
        if y2 < y1:
            y1, y2 = y2, y1
        out.append({
            "x1": max(0.0, min(1.0, x1)), "y1": max(0.0, min(1.0, y1)),
            "x2": max(0.0, min(1.0, x2)), "y2": max(0.0, min(1.0, y2)),
            "label": str(item.get("label") or item.get("class") or item.get("name") or ""),
            "score": to_float(item.get("score", item.get("conf", item.get("confidence")))) or 0.0,
        })
    return out


def _one_box(item: dict, fmt: str, fw: float, fh: float) -> tuple[float, float, float, float] | None:
    g = lambda *keys: next((float(item[k]) for k in keys if k in item), None)   # noqa: E731

    if fmt in (BOX_XYXY_NORM, BOX_XYXY_PX):
        x1, y1 = g("x1", "left", "xmin"), g("y1", "top", "ymin")
        x2, y2 = g("x2", "right", "xmax"), g("y2", "bottom", "ymax")
        if None in (x1, y1, x2, y2):
            return None
        if fmt == BOX_XYXY_PX:
            w = fw or float(item.get("width_total") or item.get("imgW") or 0) or 1.0
            h = fh or float(item.get("height_total") or item.get("imgH") or 0) or 1.0
            return x1 / w, y1 / h, x2 / w, y2 / h
        return x1, y1, x2, y2

    if fmt == BOX_XYWH_PX:
        x, y = g("x", "left"), g("y", "top")
        bw, bh = g("w", "width"), g("h", "height")
        if None in (x, y, bw, bh):
            return None
        w = fw or float(item.get("imgW") or 0) or 1.0
        h = fh or float(item.get("imgH") or 0) or 1.0
        return x / w, y / h, (x + bw) / w, (y + bh) / h

    if fmt == BOX_CXCYWH_NORM:
        cx, cy = g("cx", "x_center"), g("cy", "y_center")
        bw, bh = g("w", "width"), g("h", "height")
        if None in (cx, cy, bw, bh):
            return None
        return cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2

    return None

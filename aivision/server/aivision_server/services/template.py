"""아웃바운드 템플릿 치환 — 인바운드 매핑의 반대 방향.

인바운드(`mqtt/mapping.py`)는 `$.a.b` 로 밖에서 온 JSON 에서 **뽑아낸다**.
아웃바운드는 우리 이벤트를 상대가 원하는 모양에 **채워 넣는다**. 연산이 반대이므로 문법도
다르게 두되, 쓸 수 있는 이름을 화면에 나열해 배울 것을 줄인다.

  {event.code} {event.ts} {event.type} {event.source} {event.module} {event.confidence}
  {camera.id} {camera.name} {camera.location}
  {item.code} {item.short_name}
  {boxes_json}

없는 이름은 빈 문자열로 둔다. 템플릿 오타 하나로 발송이 통째로 실패하면 안 된다 —
상대 시스템에 값이 비어 가는 편이, 아무것도 안 가는 것보다 낫고 원인도 눈에 보인다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..models import Event
from ..timeutil import as_utc

log = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{([a-z_]+(?:\.[a-z_]+)?)\}")


def context_of(event: Event) -> dict[str, Any]:
    """이벤트에서 템플릿이 쓸 수 있는 값들을 뽑아 평면 dict 로."""
    cam = event.camera
    sol = event.solution
    ts = as_utc(event.ts)
    boxes = [{"x1": b.x1, "y1": b.y1, "x2": b.x2, "y2": b.y2,
              "label": b.label, "score": b.score} for b in event.boxes]
    return {
        "event.code": event.code,
        "event.ts": ts.isoformat().replace("+00:00", "Z") if ts else "",
        "event.type": event.event_type,
        "event.source": event.source,
        "event.module": event.module_id or "",
        "event.confidence": "" if event.confidence is None else f"{event.confidence:.3f}",
        "camera.id": event.camera_id,
        "camera.name": cam.name if cam else "",
        "camera.location": cam.location if cam else "",
        "item.code": event.solution_code,
        "item.short_name": sol.short_name if sol else event.solution_code,
        "boxes_json": json.dumps(boxes, ensure_ascii=False),
    }


def render(template: str, ctx: dict[str, Any], escape: bool = False) -> str:
    """중괄호 치환. 모르는 이름은 빈 문자열.

    escape=True 면 값을 JSON 문자열 규칙으로 이스케이프해 넣는다. 값 안의 따옴표나
    줄바꿈이 템플릿의 JSON 구조를 깨뜨리는 것을 막는다(카메라 이름에 " 가 들어 있는 경우).
    """
    if not template:
        return ""

    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in ctx:
            log.debug("템플릿에 없는 이름: {%s}", key)
            return ""
        value = ctx[key]
        if value is None:
            return ""
        text = str(value)
        # json.dumps 로 감싼 뒤 양쪽 따옴표만 떼면 이스케이프 규칙을 그대로 얻는다.
        return json.dumps(text, ensure_ascii=False)[1:-1] if escape else text

    return _PLACEHOLDER.sub(sub, template)


def render_json(template: str, ctx: dict[str, Any]) -> str:
    """페이로드용. 치환 결과가 JSON 으로 파싱되는지 확인해 준다.

    JSON 처럼 생긴 템플릿이 값 때문에 깨졌을 때는 값을 이스케이프해 한 번 더 시도한다.
    카메라 이름에 따옴표가 하나 들어 있다는 이유로 모든 발송이 깨진 JSON 이 되어서는 안 된다.
    그래도 파싱되지 않으면 그대로 보낸다 — 상대가 JSON 이 아닌 형식을 원할 수도 있다.
    다만 로그로 알려서 어드민이 오타를 눈치챌 수 있게 한다.
    """
    out = render(template, ctx)
    if not out.lstrip().startswith(("{", "[")):
        return out
    try:
        json.loads(out)
        return out
    except ValueError as exc:
        escaped = render(template, ctx, escape=True)
        try:
            json.loads(escaped)
        except ValueError:
            log.warning("아웃바운드 페이로드가 JSON 으로 파싱되지 않습니다(%s) — 그대로 보냅니다",
                        exc)
            return out
        log.info("아웃바운드 페이로드의 값을 이스케이프했습니다 (원본은 JSON 이 아니었음: %s)",
                 exc)
        return escaped


# 화면에서 안내로 보여 줄 목록. 여기와 context_of 가 어긋나지 않게 한곳에서 만든다.
AVAILABLE_FIELDS: tuple[str, ...] = (
    "event.code", "event.ts", "event.type", "event.source", "event.module",
    "event.confidence", "camera.id", "camera.name", "camera.location",
    "item.code", "item.short_name", "boxes_json",
)

DEFAULT_TOPIC_TEMPLATE = "aivision/event/{event.code}"
DEFAULT_PAYLOAD_TEMPLATE = (
    '{\n'
    '  "event": "{event.code}",\n'
    '  "ts": "{event.ts}",\n'
    '  "camera": {camera.id},\n'
    '  "location": "{camera.location}",\n'
    '  "item": "{item.code}",\n'
    '  "type": "{event.type}",\n'
    '  "confidence": "{event.confidence}",\n'
    '  "boxes": {boxes_json}\n'
    '}'
)

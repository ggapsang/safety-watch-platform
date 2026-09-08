"""ONVIF-over-MQTT 페이로드 파서.

한화비전 카메라는 ONVIF 이벤트를 MQTT 로 흘린다. 토픽은 장치 MAC 을 접두로 갖고,
그 뒤에 ONVIF 이벤트 네임스페이스가 붙는다.

  E4:30:22:F3:31:AA/onvif-ej/Device/tns1:Trigger/tns1:Relay/&Relay-1
  └── 장치 MAC ──┘└──────── ONVIF 이벤트 토픽 ─────────────────┘

페이로드는 두 가지 형태가 모두 관측된다. 둘 다 같은 평면 dict 로 정규화한다.

  (A) 축약형   {"UtcTime":"…","Source":{"RelayToken":"Relay-1"},
                "Data":{"LogicalState":"active"}}
  (B) ONVIF 원형 {"UtcTime":"…",
                "Source":{"SimpleItem":[{"Name":"VideoSourceConfigurationToken","Value":"…"}]},
                "Data":{"SimpleItem":[{"Name":"State","Value":"true"}]}}

빈 페이로드(예: `{MAC}/heartbeat`, `{MAC}/fireAlarm`)도 정상이다 — '수신 자체가 신호'인 경우다.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

# MAC 주소 접두 (콜론·하이픈 모두 허용)
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}(?::|-)){5}[0-9A-Fa-f]{2}")

# 참(활성)으로 볼 값들. ONVIF SimpleItem 의 State 는 "true"/"false" 로 온다.
_TRUE = {"active", "true", "1", "on", "yes"}
_FALSE = {"inactive", "false", "0", "off", "no"}


def split_topic(topic: str) -> tuple[str, str]:
    """토픽 → (MAC, 나머지). MAC 접두가 없으면 ('', 원본)."""
    m = _MAC_RE.match(topic)
    if not m:
        return "", topic
    mac = m.group(0).upper().replace("-", ":")
    rest = topic[m.end():]
    return mac, rest


def parse_payload(raw: bytes | str) -> dict[str, Any]:
    """페이로드 → dict. JSON 이 아니면 {'_raw': 원문} 으로 감싼다."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8", errors="replace")
        except Exception:                                   # noqa: BLE001
            return {}
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"_raw": raw}
    return data if isinstance(data, dict) else {"_raw": raw}


def _flatten_section(section: Any) -> dict[str, Any]:
    """Source/Data 섹션을 {이름: 값} 평면 dict 로."""
    out: dict[str, Any] = {}
    if not isinstance(section, dict):
        return out
    items = section.get("SimpleItem")
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and "Name" in it:
                out[str(it["Name"])] = it.get("Value")
    elif isinstance(items, dict) and "Name" in items:
        out[str(items["Name"])] = items.get("Value")
    for k, v in section.items():
        if k == "SimpleItem":
            continue
        out.setdefault(k, v)
    return out


def normalize(payload: dict[str, Any]) -> dict[str, Any]:
    """축약형·ONVIF 원형을 같은 평면 dict 로 정규화한다.

    반환 예: {'UtcTime': …, 'Source.RelayToken': 'Relay-1', 'Data.LogicalState': 'active'}
    """
    flat: dict[str, Any] = {}
    for key in ("UtcTime", "Topic", "PropertyOperation"):
        if key in payload:
            flat[key] = payload[key]
    for section in ("Source", "Key", "Data"):
        for name, value in _flatten_section(payload.get(section)).items():
            flat[f"{section}.{name}"] = value
    for k, v in payload.items():
        if k not in ("Source", "Key", "Data") and k not in flat:
            flat[k] = v
    return flat


def lookup(flat: dict[str, Any], path: str) -> Any:
    """정규화된 dict 에서 값 조회. 'Data.LogicalState' 또는 'LogicalState' 둘 다 받는다."""
    if not path:
        return None
    if path in flat:
        return flat[path]
    tail = path.rsplit(".", 1)[-1]
    for k, v in flat.items():
        if k == tail or k.endswith("." + tail):
            return v
    return None


def truthy(value: Any, active: str = "active", inactive: str = "inactive") -> bool | None:
    """값 → 활성 여부. 판단이 불가능하면 None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s == (active or "").strip().lower():
        return True
    if s == (inactive or "").strip().lower():
        return False
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


def event_time(flat: dict[str, Any]) -> datetime:
    """페이로드의 UtcTime 을 우선 쓰고, 없거나 이상하면 수신 시각을 쓴다.

    주의: 카메라 시계가 틀어져 있으면 통계가 통째로 어긋난다. 미래 시각이거나 1일 이상
      과거면 신뢰하지 않고 수신 시각으로 대체한다.
    """
    now = datetime.now(timezone.utc)
    raw = flat.get("UtcTime")
    if not raw:
        return now
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return now
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    drift = (ts - now).total_seconds()
    if drift > 60 or drift < -86400:
        return now
    return ts

"""인바운드 완충장치 — 같은 메시지가 쏟아질 때 앞에서 막는다.

**왜 중복 억제(event_dedup_sec)로 부족한가.** 그것은 이벤트 *승격* 단계에서 DB 를 조회해
같은 (카메라, 항목) 이벤트가 있는지 본다. 결과는 맞지만 값이 비싸다 — 메시지 하나마다
바인딩 평가와 DB 왕복이 붙는다. 초당 수백 개가 들어오면 막아 주는 그 일이 곧 부하가 된다.

여기는 그보다 앞, **문 앞**이다. 들어온 메시지가 직전에 온 것과 글자 그대로 같으면
정해진 간격 안에서는 통째로 버린다. 바인딩도, DB 도, 원문 적재도 타지 않는다.

  중복 억제 : "같은 상황을 두 번 기록하지 않는다"   — 의미 수준, 20초, DB 로 판단
  완충장치  : "같은 메시지를 두 번 처리하지 않는다" — 전송 수준, 1초, 메모리로 판단

**왜 (토픽 + 페이로드) 로 묶는가.** '같은 메시지' 를 글자 그대로 해석한다. 그래야
라이브 박스처럼 매번 내용이 달라지는 트래픽은 건드리지 않고(좌표가 매번 다르다),
한화비전이 이따금 뿜는 **빈 페이로드 연발**과 고정 문구 알람만 정확히 걸린다.

간격 안에 들어온 것을 큐에 쌓았다가 나중에 처리하지 않는다. 같은 메시지이므로 나중에
처리해도 결과가 같고, 쌓아 두면 폭주할 때 메모리가 대신 터진다. 버리는 편이 맞다.
"""

from __future__ import annotations

import hashlib
import logging
import time

from ..config import get_settings
from ..db import sessionmaker
from . import settings_store as store

log = logging.getLogger(__name__)

# 이 수를 넘으면 오래된 것부터 정리한다. 토픽이 무한히 늘어나는 소스를 만나도
# 메모리가 늘지 않아야 한다.
MAX_KEYS = 20_000

_interval: float = 1.0
_seen: dict[tuple[str, str], float] = {}
dropped = 0
passed = 0


async def reload() -> None:
    """설정을 읽어 캐시한다. 메시지마다 DB 를 보면 완충장치가 부하가 된다."""
    global _interval
    s = get_settings()
    async with sessionmaker()() as session:
        runtime = await store.get_runtime(session)
    try:
        value = float(runtime.get("inbound_min_interval_sec", s.inbound_min_interval_sec))
    except (TypeError, ValueError):
        value = s.inbound_min_interval_sec
    value = max(0.0, value)
    if value != _interval:
        log.info("인바운드 완충 간격: %.2f초%s", value, " (끔)" if value <= 0 else "")
    _interval = value


def _digest(payload: bytes | str | None) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        payload = payload.encode("utf-8", "replace")
    elif not isinstance(payload, bytes):
        payload = str(payload).encode("utf-8", "replace")
    # 페이로드를 통째로 들고 있으면 큰 메시지에서 메모리가 는다. 지문만 남긴다.
    return hashlib.blake2b(payload, digest_size=16).hexdigest()


def allow(topic: str, payload: bytes | str | None) -> bool:
    """이 메시지를 지금 처리할 것인가. False 면 버린다."""
    global dropped, passed
    if _interval <= 0:
        passed += 1
        return True

    key = (topic, _digest(payload))
    now = time.monotonic()
    last = _seen.get(key)
    if last is not None and now - last < _interval:
        dropped += 1
        return False

    _seen[key] = now
    passed += 1
    if len(_seen) > MAX_KEYS:
        _prune(now)
    return True


def _prune(now: float) -> None:
    """오래된 기록을 버린다. 간격보다 오래된 것은 어차피 다시 통과시킬 것들이다."""
    cutoff = now - max(_interval, 1.0) * 10
    stale = [k for k, t in _seen.items() if t < cutoff]
    for k in stale:
        del _seen[k]
    if len(_seen) > MAX_KEYS:
        # 그래도 많으면 오래된 절반을 통째로 버린다. 완충장치가 메모리를 먹으면 본말전도다.
        for k, _ in sorted(_seen.items(), key=lambda kv: kv[1])[: len(_seen) // 2]:
            del _seen[k]
    log.debug("완충장치 정리: %d개 유지", len(_seen))


def status() -> dict:
    return {"interval_sec": _interval, "tracked": len(_seen),
            "passed": passed, "dropped": dropped}

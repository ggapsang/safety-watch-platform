"""수신 원문 적재 정책 — 무엇을 DB 에 남길 것인가.

**기본은 남기지 않는다.** 필요할 때만 켠다.

왜 기본이 끔인가. 라이브 박스처럼 초당 여러 번 들어오는 통과 트래픽을 기본으로 적재하면
카메라 두 대만으로 하루 수십만 줄이 쌓이고 메시지마다 DB 커밋이 붙는다. 그리고 정작 원문
로그를 둔 이유(사후에 '그때 무엇이 왔었나'를 되짚는 것)도 그 잡음에 묻힌다.

끄고 있어도 '모르는 토픽 찾기'는 막히지 않는다. 'MQTT 로그' 화면은 브로커에 **직결 구독**
하므로 실시간으로 무엇이 오는지 보는 데는 DB 가 필요 없다. DB 사본은 지난 것을 되짚을
때만 쓰인다 — 그래서 기본을 끔으로 두고, 필요한 채널만 골라 켜는 편이 맞다.

  off        아무것도 남기지 않는다 (기본)
  unmatched  바인딩에 안 걸린 것만 남긴다 — 새 소스를 붙이는 동안 쓰기 좋다
  all        전부 남긴다 — 짧게 켰다 끄는 용도

토픽 패턴(`mqtt_log_topics`)을 채우면 모드보다 우선한다. '이 채널만 남긴다'는 분명한
의사표시이기 때문이다.

정책은 캐시한다. 메시지마다 설정을 조회하면 초당 수십 번 DB 를 보게 된다. 설정이 바뀌면
`reload()` 가 불린다(설정 저장 API 와 탐지 소스 재적재 경로 양쪽).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import get_settings
from ..db import sessionmaker
from ..models import MqttMessage
from ..mqtt import mapping
from ..timeutil import now_utc
from . import settings_store as store

log = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_UNMATCHED = "unmatched"
MODE_ALL = "all"
VALID_MODES = (MODE_OFF, MODE_UNMATCHED, MODE_ALL)

_mode: str = MODE_OFF
_topics: list[str] = []
skipped = 0                      # 정책에 따라 남기지 않은 수. '안 왔다'와 다르다
stored = 0


async def reload() -> None:
    global _mode, _topics
    s = get_settings()
    async with sessionmaker()() as session:
        runtime = await store.get_runtime(session)
    mode = str(runtime.get("mqtt_log_mode", s.mqtt_log_mode) or MODE_OFF).lower()
    if mode not in VALID_MODES:
        log.warning("알 수 없는 원문 적재 모드 '%s' — off 로 진행합니다", mode)
        mode = MODE_OFF
    raw = str(runtime.get("mqtt_log_topics", s.mqtt_log_topics) or "")
    topics = [t.strip() for t in raw.split(",") if t.strip()]
    if (mode, topics) != (_mode, _topics):
        log.info("원문 적재 정책: %s%s", mode,
                 f" (지정 채널 {', '.join(topics)})" if topics else "")
    _mode, _topics = mode, topics


def should_log(topic: str, *, matched: bool) -> bool:
    if _topics:
        return any(mapping.topic_matches(p, topic) for p in _topics)
    if _mode == MODE_ALL:
        return True
    if _mode == MODE_UNMATCHED:
        return not matched
    return False


async def store_message(topic: str, payload: Any, camera_id: int | None,
                        *, matched: bool) -> bool:
    """정책이 허락하면 원문을 남긴다. 남겼는지 반환."""
    global skipped, stored
    if not should_log(topic, matched=matched):
        skipped += 1
        return False
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", errors="replace")
    elif isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, ensure_ascii=False)
    async with sessionmaker()() as session:
        session.add(MqttMessage(ts=now_utc(), topic=topic[:400], payload=text[:8000],
                                camera_id=camera_id, matched=matched))
        await session.commit()
    stored += 1
    return True


def status() -> dict:
    return {"mode": _mode, "topics": list(_topics), "stored": stored, "skipped": skipped}

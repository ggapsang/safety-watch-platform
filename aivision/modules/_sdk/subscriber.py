"""구독 전용 MQTT 연결 — 남이 낸 판독을 받아 쓰는 플러그인을 위한 것.

**왜 필요한가.** 지금까지 플러그인은 전부 `RTSP -> 박스` 한 모양이었다. 그런데 규칙을
따지는 플러그인은 영상이 아니라 **다른 플러그인이 이미 낸 박스**를 먹어야 한다. 같은
프레임을 두 번 추론하는 것은 낭비이고, 사람처럼 카메라가 이미 잘 잡는 것을 우리가 다시
학습할 이유도 없다.

**코어는 이 일을 모른다.** `aivision/live/{camera_id}` 는 처음부터 브로커 위의 공개
토픽이었고 지금까지 코어만 읽고 있었을 뿐이다. 여기서 하는 일은 새 통로를 뚫는 것이
아니라 *그 토픽을 남이 읽어도 된다*는 약속을 코드로 옮기는 것이다. 중계자를 코어에 두면
코어가 파이프라인을 알게 된다(매니페스토 2번).

**받은 것을 그대로 믿지 않는다.** 발행자는 다른 팀이 만든 플러그인일 수 있다. 모양이
계약과 다르면 조용히 버린다 — 여기서 예외를 올리면 남의 버그로 내 플러그인이 죽는다.

    from _sdk import LiveSubscriber

    sub = LiveSubscriber(cfg.mqtt_host, cfg.mqtt_port, "collision")
    sub.on_boxes(lambda f: print(f.camera_id, f.module_id, f.boxes))
    sub.start(cameras=[1, 2], modules=["yolo-server", "camera-meta"])
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)

LIVE_TOPIC_ANY = "aivision/live/+"
LIVE_TOPIC_ONE = "aivision/live/{camera_id}"


@dataclass
class Frame:
    """한 발행자가 한 순간에 본 것.

    `received` 를 따로 두는 이유: 발행자의 `ts` 는 그쪽 시계이고 초 단위까지만 적힐 수도
    있다. 서로 다른 발행자의 판독을 시간으로 맞춰야 하는 쪽에서는 도착 시각이 더 쓸 만하다.
    둘 다 준다 — 무엇을 쓸지는 받는 쪽이 정한다.
    """

    camera_id: int
    module_id: str
    ts: str                                  # 발행자가 적은 시각 (ISO8601)
    boxes: list[dict] = field(default_factory=list)
    received: float = 0.0                    # 받은 시각 (time.monotonic)

    def labels(self) -> set[str]:
        return {str(b.get("label", "")) for b in self.boxes if b.get("label")}

    def of(self, *labels: str) -> list[dict]:
        """이 라벨들만 고른다. 라벨은 발행자가 화면에 쓰라고 붙인 이름이라 현장에서
        바뀔 수 있다 — 고정된 코드가 아니다. 설정으로 받아 넘기는 편이 안전하다."""
        want = set(labels)
        return [b for b in self.boxes if str(b.get("label", "")) in want]


def parse_frame(topic: str, payload: bytes) -> Frame | None:
    """받은 메시지 -> Frame. 계약과 다르면 None.

    좌표까지 검사한다. 0~1 정규화는 계약이고, 픽셀 좌표를 그대로 받아 쓰면 프레임 크기를
    모르는 쪽에서 엉뚱한 거리를 재게 된다. 틀린 값으로 판정하느니 안 받는 편이 낫다.
    """
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.debug("JSON 이 아닌 페이로드: %s", topic)
        return None
    if not isinstance(data, dict):
        return None

    camera_id = data.get("camera_id")
    if camera_id is None:
        # 토픽 끝자리가 카메라 번호라는 것이 계약이다. 페이로드가 빠뜨렸으면 거기서 읽는다.
        tail = topic.rsplit("/", 1)[-1]
        camera_id = tail if tail.isdigit() else None
    try:
        camera_id = int(camera_id)
    except (TypeError, ValueError):
        return None

    boxes = []
    for b in data.get("boxes") or []:
        if not isinstance(b, dict):
            continue
        try:
            xy = [float(b[k]) for k in ("x1", "y1", "x2", "y2")]
        except (KeyError, TypeError, ValueError):
            continue
        if not all(-0.001 <= v <= 1.001 for v in xy):
            log.debug("정규화되지 않은 좌표를 버립니다: %s", b)
            continue
        boxes.append(b)

    return Frame(camera_id=camera_id, module_id=str(data.get("module_id") or ""),
                 ts=str(data.get("ts") or ""), boxes=boxes, received=time.monotonic())


class LiveSubscriber:
    """`aivision/live/+` 구독. 필요한 카메라·발행자만 골라 받는다.

    콜백은 MQTT 네트워크 스레드에서 불린다. 무거운 일을 하면 그 사이 메시지가 밀리므로,
    받은 것을 자기 자료구조에 넣고 판정은 자기 스레드에서 하는 편이 낫다.
    """

    def __init__(self, host: str, port: int, client_id: str) -> None:
        import paho.mqtt.client as mqtt

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"{client_id}-sub")
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_message = self._on_message
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = lambda *_: log.warning("브로커 연결 끊김 — 재연결 시도")
        self.host, self.port = host, port

        self._handlers: list[Callable[[Frame], None]] = []
        self._cameras: set[int] | None = None      # None 이면 전부
        self._modules: set[str] | None = None      # None 이면 전부
        self._topics: list[str] = [LIVE_TOPIC_ANY]
        self._lock = threading.Lock()
        self.received = 0
        self.dropped = 0

    # ── 설정 ────────────────────────────────────────────────────────

    def on_boxes(self, handler: Callable[[Frame], None]) -> None:
        self._handlers.append(handler)

    def start(self, *, cameras: list[int] | None = None,
              modules: list[str] | None = None) -> None:
        """구독 시작.

        `cameras` 를 주면 그 카메라 토픽만 구독한다 — 카메라가 수십 대인 현장에서
        전부 받아 버리면 쓰지도 않을 메시지를 초당 수백 건 푼다.
        `modules` 는 토픽으로 가릴 수 없어(발행자가 페이로드 안에 있다) 받은 뒤 거른다.
        """
        with self._lock:
            self._cameras = set(cameras) if cameras else None
            self._modules = set(modules) if modules else None
            self._topics = ([LIVE_TOPIC_ONE.format(camera_id=c) for c in cameras]
                            if cameras else [LIVE_TOPIC_ANY])
        try:
            self.client.connect_async(self.host, self.port, keepalive=60)
            self.client.loop_start()
        except OSError as exc:
            log.warning("브로커 연결 실패(%s) — 계속 재시도합니다", exc)

    def retarget(self, cameras: list[int]) -> None:
        """담당 카메라가 바뀌었다. 플랫폼의 일감이 바뀌면 여기도 따라가야 한다."""
        with self._lock:
            old, self._cameras = self._topics, set(cameras) if cameras else None
            self._topics = ([LIVE_TOPIC_ONE.format(camera_id=c) for c in cameras]
                            if cameras else [LIVE_TOPIC_ANY])
            new = self._topics
        if old == new:
            return
        for t in old:
            self.client.unsubscribe(t)
        for t in new:
            self.client.subscribe(t, qos=0)
        log.info("구독 변경: %s", ", ".join(new))

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def status(self) -> dict:
        return {"topics": list(self._topics), "received": self.received,
                "dropped": self.dropped}

    # ── 내부 ────────────────────────────────────────────────────────

    def _on_connect(self, *_args) -> None:
        log.info("브로커 연결: %s:%d — 구독 %s", self.host, self.port,
                 ", ".join(self._topics))
        for t in self._topics:                 # 재연결 때마다 다시 건다
            self.client.subscribe(t, qos=0)

    def _on_message(self, _client, _userdata, msg) -> None:
        frame = parse_frame(msg.topic, msg.payload)
        if frame is None:
            self.dropped += 1
            return
        with self._lock:
            cams, mods = self._cameras, self._modules
        if cams is not None and frame.camera_id not in cams:
            return
        if mods is not None and frame.module_id not in mods:
            return
        self.received += 1
        for handler in self._handlers:
            try:
                handler(frame)
            except Exception:                  # noqa: BLE001
                # 남의 콜백이 터져도 구독은 살아 있어야 한다. 죽으면 조용히 아무것도
                # 안 받는 상태가 되고, 그게 가장 찾기 어려운 고장이다.
                log.exception("구독 콜백에서 예외")

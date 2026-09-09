"""모듈 수명주기 — 등록 · 일감 폴링 · 워커 관리 · heartbeat.

모듈이 구현할 것은 `Source` 하나다. 나머지는 전부 여기서 돈다.

카메라 한 대를 스레드 하나가 맡는다. 프레임 읽기도 메타데이터 읽기도 블로킹이고 GIL
밖에서 도므로, asyncio 로 감싸는 것보다 스레드가 정직하다. 무거운 자원(모델 세션,
디코더)은 스레드마다 따로 연다 — 여러 스레드에서 공유하면 조용히 느려지거나 깨진다.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from typing import Iterator

from .platform import Platform, WorkItem
from .publisher import Publisher

log = logging.getLogger(__name__)


class Source:
    """모듈이 구현하는 것 — '이 카메라를 보고 박스를 내놓아라'.

    `boxes()` 는 제너레이터다. 한 번 내놓을 때마다 그 시점에 보이는 박스 전부를 준다.
    빈 리스트도 의미가 있다(아무 것도 안 보인다). 스트림이 끊기면 그냥 끝내면 되고,
    다시 부르는 것은 러너의 책임이다.

    박스 하나의 모양(플랫폼 저장 규약과 같다):
        {"x1": .., "y1": .., "x2": .., "y2": .., "label": "..", "score": 0.0~1.0}
    좌표는 **0~1 정규화**. 픽셀로 주지 않는다 — 프레임 크기는 스트림 프로파일에 따라
    바뀌고, 그것을 아는 쪽은 보내는 쪽이다.
    """

    def open(self, item: WorkItem) -> None:
        """무거운 자원을 여기서 연다. 워커 스레드 안에서 불린다."""

    def boxes(self, item: WorkItem, stop: threading.Event) -> Iterator[list[dict]]:
        raise NotImplementedError

    def close(self) -> None:
        """열었던 것을 정리한다. 예외가 나도 러너가 삼킨다."""

    @property
    def device(self) -> str:
        """상태 화면에 보여 줄 실제 사용 자원. 없으면 '-'."""
        return "-"


class SourceWorker(threading.Thread):
    """카메라 한 대. 소스가 내놓는 박스를 정해진 통로로 내보낸다.

    `on_boxes` 를 주면 라이브 발행 외에 추가 처리를 할 수 있다(예: 전이 검출 후
    이벤트 발행). 주지 않으면 **라이브 박스만 나간다** — 그것이 기본값인 이유는,
    초당 여러 번 나오는 판독을 이벤트로 쌓으면 DB 가 무너지기 때문이다.
    """

    def __init__(self, item: WorkItem, cfg, publisher: Publisher, source: Source,
                 on_boxes=None, on_stream_end=None) -> None:
        super().__init__(name=f"cam{item.camera_id}", daemon=True)
        self.item = item
        self.cfg = cfg
        self.pub = publisher
        self.source = source
        self.on_boxes = on_boxes
        # 스트림이 끊겼을 때 정리할 것이 있는 모듈을 위한 훅. 예를 들어 전이를 추적하는
        # 모듈은 여기서 '해제' 를 내보내야 한다 — 안 그러면 화면에 발생 상태가 영원히
        # 켜져 있다. 무엇을 정리할지는 모듈만 안다.
        self.on_stream_end = on_stream_end
        self.stop_event = threading.Event()
        self.frames = 0
        self.published = 0
        self.last_error = ""
        self._last_live = 0.0

    def stop(self) -> None:
        self.stop_event.set()

    @property
    def device(self) -> str:
        return self.source.device

    def emit_live(self, boxes: list[dict], *, force: bool = False) -> None:
        """라이브 박스 발행. 최소 간격으로 눌러 브라우저를 보호한다."""
        if not self.cfg.publish_live:
            return
        now = time.monotonic()
        if not force and now - self._last_live < self.cfg.live_min_interval:
            return
        self._last_live = now
        self.pub.publish_live(self.cfg.module_id, self.item.camera_id, boxes)
        self.published += 1

    def run(self) -> None:
        log.info("카메라 %d(%s) 워커 시작 — %s", self.item.camera_id,
                 self.item.camera_name, self.item.rtsp or "(주소 없음)")
        backoff = 1.0
        try:
            while not self.stop_event.is_set():
                try:
                    self.source.open(self.item)
                    for boxes in self.source.boxes(self.item, self.stop_event):
                        self.frames += 1
                        self.last_error = ""
                        backoff = 1.0
                        if self.on_boxes is not None:
                            self.on_boxes(self, boxes)
                        self.emit_live(boxes)
                        if self.stop_event.is_set():
                            break
                except Exception as exc:                        # noqa: BLE001
                    self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                    log.warning("카메라 %d 소스 오류 — %.0fs 후 재시도: %s",
                                self.item.camera_id, backoff, self.last_error)
                finally:
                    if self.on_stream_end is not None:
                        try:
                            self.on_stream_end(self)
                        except Exception:                       # noqa: BLE001
                            log.exception("스트림 종료 처리 실패")
                    try:
                        self.source.close()
                    except Exception:                           # noqa: BLE001
                        log.debug("소스 정리 중 오류", exc_info=True)

                if self.stop_event.is_set():
                    break
                # 스트림이 끊긴 채 박스가 남아 있으면 화면에 영원히 켜져 있다. 비워 준다.
                self.emit_live([], force=True)
                if self.stop_event.wait(backoff):
                    break
                backoff = min(backoff * 2, 30.0)
        finally:
            self.emit_live([], force=True)
            log.info("카메라 %d 워커 종료 (수신 %d, 발행 %d)",
                     self.item.camera_id, self.frames, self.published)


class Runner:
    """모듈 본체. `make_source` 는 워커마다 새 소스를 만들어 준다."""

    def __init__(self, cfg, make_source, on_boxes=None, on_stream_end=None,
                 kind: str = "sidecar", description: str = "") -> None:
        self.cfg = cfg
        self.make_source = make_source
        self.on_boxes = on_boxes
        self.on_stream_end = on_stream_end
        self.kind = kind
        self.description = description
        self.platform = Platform(cfg.platform_url, cfg.module_id)
        self.publisher = Publisher(cfg.mqtt_host, cfg.mqtt_port, cfg.module_id)
        self.workers: dict[int, SourceWorker] = {}
        self.stop_event = threading.Event()

    def reconcile(self, items: list[WorkItem]) -> None:
        """일감과 현재 워커를 맞춘다. 할당이 늘거나 줄면 여기서 따라간다."""
        wanted = {i.camera_id: i for i in items}
        for cam_id in list(self.workers):
            worker = self.workers[cam_id]
            if cam_id not in wanted or not worker.is_alive():
                worker.stop()
                self.workers.pop(cam_id, None)
                if cam_id in wanted:
                    log.info("카메라 %d 워커가 죽어 있어 다시 띄웁니다", cam_id)
        for cam_id, item in wanted.items():
            if cam_id in self.workers:
                continue
            worker = SourceWorker(item, self.cfg, self.publisher,
                                  self.make_source(item), self.on_boxes,
                                  self.on_stream_end)
            self.workers[cam_id] = worker
            worker.start()

    def status(self) -> dict:
        devices = sorted({w.device for w in self.workers.values()})
        return {
            "cameras": sorted(self.workers),
            "device": ", ".join(devices) or "-",
            "frames": sum(w.frames for w in self.workers.values()),
            "published": sum(w.published for w in self.workers.values()),
            "errors": {str(c): w.last_error for c, w in self.workers.items()
                       if w.last_error},
        }

    def run(self) -> None:
        cfg = self.cfg
        self.publisher.start()

        while not self.stop_event.is_set():
            if self.platform.register(cfg.module_name, cfg.capabilities,
                                      self.kind, self.description):
                break
            log.warning("플랫폼 등록 실패 — 5초 후 재시도 (%s)", cfg.platform_url)
            if self.stop_event.wait(5.0):
                return
        log.info("등록 완료: %s (판정 항목 %s)", cfg.module_id,
                 ", ".join(cfg.capabilities) or "없음")

        last_heartbeat = 0.0
        while not self.stop_event.is_set():
            items = self.platform.work()
            if items is None:
                # 플랫폼이 안 보인다. 돌고 있는 워커는 그대로 둔다 — 영상과 브로커는
                # 플랫폼 REST 와 무관하게 살아 있을 수 있다.
                log.debug("일감을 받지 못했습니다 — 현재 워커를 유지합니다")
            else:
                if not items:
                    log.info("할당된 카메라가 없습니다. 플랫폼에서 모듈에 카메라를 할당하세요.")
                self.reconcile(items)

            now = time.monotonic()
            if now - last_heartbeat >= cfg.heartbeat_sec:
                last_heartbeat = now
                self.platform.heartbeat(self.status())

            self.stop_event.wait(cfg.work_poll_sec)

        self.shutdown()

    def shutdown(self) -> None:
        log.info("종료 중…")
        for worker in self.workers.values():
            worker.stop()
        for worker in self.workers.values():
            worker.join(timeout=5.0)
        self.publisher.stop()
        log.info("종료 완료")


def main_loop(runner: Runner) -> int:
    """SIGTERM/SIGINT 를 받아 정리하고 끝낸다. 모듈 main() 이 그대로 쓴다."""
    signal.signal(signal.SIGTERM, lambda *_: runner.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: runner.stop_event.set())
    try:
        runner.run()
    except KeyboardInterrupt:
        runner.stop_event.set()
        runner.shutdown()
    return 0


def configure_logging(level: str = "") -> None:
    logging.basicConfig(
        level=getattr(logging, (level or os.environ.get("LOG_LEVEL", "INFO")).upper(),
                      logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")


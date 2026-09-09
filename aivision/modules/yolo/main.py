"""서버 YOLO 사이드카 — 영상을 받아 추론하고 전이만 발행한다.

배관(등록·일감 수령·워커 수명주기·발행·heartbeat)은 `_sdk` 가 진다. 여기 남는 것은
이 모듈만 아는 것 셋이다.

  · 프레임을 어떻게 얻고 무엇으로 추론하는가        YoloSource
  · 모델이 없을 때 배관만 돌려 보는 방법            DryRunSource
  · 언제 이벤트로 올리는가                          on_boxes / on_stream_end

**라이브 박스와 이벤트를 둘 다 낸다**는 점이 카메라 메타데이터 모듈과 다르다.
저쪽은 카메라가 이미 판정해 둔 것을 그리기만 하지만, 여기는 우리가 판정하므로
'무엇이 사건인가'를 정할 수 있다. 다만 **전이 순간만** 올린다 — 사람이 서 있는 동안
초당 여러 번 나오는 판독을 이벤트로 쌓으면 DB 가 무너진다.

영상은 카메라가 아니라 미디어 서버에서 가져온다. 일감(/work)이 준 주소를 그대로 쓰므로
카메라 계정을 알 필요가 없고 카메라 세션도 늘지 않는다.

실행
  python main.py                  모델이 있으면 추론, 없으면 dry-run 으로 내려앉는다
  python main.py --dry-run        모델을 무시하고 합성 박스를 발행 (전 경로 점검용)
"""

from __future__ import annotations

import logging
import random
import sys
import threading
import time
from typing import Iterator

sys.path.insert(0, "/app")                     # _sdk 가 옆에 놓인다

import config as config_module                 # noqa: E402
from _sdk import (Debouncer, Runner, Source, WorkItem, configure_logging,  # noqa: E402
                  main_loop)

log = logging.getLogger("yolo")


class _Base(Source):
    """두 소스가 함께 쓰는 것 — 항목별 묶음과 전이 판정.

    `last_found` 는 마지막으로 내놓은 박스를 '탐지 항목별' 로 묶어 둔 것이다.
    라이브 박스는 클래스 이름을 그대로 보여 주는 편이 사람이 읽기 좋고, 이벤트는 항목
    코드로 올려야 하므로 두 모양이 다르다. 그 차이를 소스가 들고 있는다.
    """

    def __init__(self, cfg, item: WorkItem) -> None:
        self.cfg = cfg
        options = item.options or {}
        self.debouncer = Debouncer(
            hold_sec=float(options.get("hold_sec", cfg.hold_sec)),
            min_conf=float(options.get("min_conf", cfg.min_conf)))
        self.sample_fps = max(0.2, float(options.get("sample_fps", cfg.sample_fps)))
        self.interval = 1.0 / self.sample_fps
        self.last_found: dict[str, list[dict]] = {}


class DryRunSource(_Base):
    """모델 없이 합성 박스를 낸다.

    영상을 열지 않는다. 카메라가 꺼져 있어도 등록 -> 일감 -> 발행 -> 바인딩 -> 이벤트
    전 경로를 확인할 수 있어야 한다는 것이 dry-run 의 목적이다.
    """

    @property
    def device(self) -> str:
        return "dry-run"

    def boxes(self, item: WorkItem, stop: threading.Event) -> Iterator[list[dict]]:
        items = self.cfg.capabilities
        if not items:
            log.error("dry-run 인데 CLASS_MAP 이 비어 있어 발행할 항목이 없습니다. "
                      "플랫폼에서 탐지 항목을 만들고 CLASS_MAP 을 채우세요.")
            return

        phase = 0.0
        while not stop.wait(self.interval):
            phase += self.interval
            # 15초 켜고 10초 끄기를 반복한다. 발생과 해제가 모두 나와야 검증이 된다.
            on = (phase % 25.0) < 15.0
            code = items[int(phase // 25.0) % len(items)]
            boxes = [self._synthetic_box(code, phase)] if on else []
            self.last_found = {code: boxes} if on else {}
            yield boxes

    @staticmethod
    def _synthetic_box(label: str, phase: float) -> dict:
        """화면을 천천히 가로지르는 박스. 오버레이가 살아 있는지 눈으로 본다."""
        x = 0.05 + 0.6 * ((phase / 12.0) % 1.0)
        y = 0.25 + 0.1 * random.random()
        return {"x1": round(x, 4), "y1": round(y, 4),
                "x2": round(x + 0.22, 4), "y2": round(y + 0.35, 4),
                "label": label, "score": round(0.72 + 0.2 * random.random(), 3)}


class YoloSource(_Base):
    """RTSP 프레임을 솎아 추론한다. 모델은 워커 스레드마다 따로 연다."""

    def __init__(self, cfg, item: WorkItem) -> None:
        super().__init__(cfg, item)
        self.model = None
        self.cap = None

    @property
    def device(self) -> str:
        return getattr(self.model, "device", "-") if self.model else "-"

    def open(self, item: WorkItem) -> None:
        import cv2
        import inference

        if self.model is None:
            self.model = inference.load(self.cfg.model_path, self.cfg.device,
                                        self.cfg.imgsz, self.cfg.conf_thres,
                                        self.cfg.iou_thres, self.cfg.class_map,
                                        self.cfg.layout)
        if not item.rtsp:
            raise RuntimeError("일감에 스트림 주소가 없습니다")
        cap = cv2.VideoCapture(item.rtsp, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"스트림을 열 수 없습니다: {item.rtsp}")
        # 큐를 짧게 둔다. 밀린 프레임을 따라잡느라 지연이 누적되면 안전관리에서 쓸 수 없다.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap = cap
        log.info("카메라 %d 스트림 연결: %s", item.camera_id, item.rtsp)

    def close(self) -> None:
        cap, self.cap = self.cap, None
        if cap is not None:
            cap.release()

    def boxes(self, item: WorkItem, stop: threading.Event) -> Iterator[list[dict]]:
        cap, model = self.cap, self.model
        if cap is None or model is None:
            raise RuntimeError("스트림이 열리지 않았습니다")

        while not stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError("프레임 읽기 실패")

            live: list[dict] = []
            found: dict[str, list[dict]] = {}
            for det in model.infer(frame):
                box = det.to_box()
                live.append(box)                       # 화면에는 클래스 이름 그대로
                code = model.item_code(det.cls)
                if code:                               # 표에 없는 클래스는 이 현장의 관심 밖
                    found.setdefault(code, []).append(dict(box, label=code))
            self.last_found = found
            yield live

            if stop.wait(self.interval):
                break


# ────────────────────────────────────────────────────────────── 이벤트로 올리기

def on_boxes(worker, _boxes: list[dict]) -> None:
    """한 프레임 결과를 전이 판정에 넣고, 바뀐 것만 발행한다."""
    src = worker.source
    _emit(worker, src.debouncer.observe(worker.item.camera_id, time.monotonic(),
                                        src.last_found))


def on_stream_end(worker) -> None:
    """스트림이 끊기면 켜져 있던 것을 해제한다.

    이게 없으면 '발생' 만 남고 '해제' 가 오지 않아 화면에 영원히 켜져 있다.
    """
    _emit(worker, worker.source.debouncer.close_camera(worker.item.camera_id,
                                                       time.monotonic()))


def _emit(worker, transitions) -> None:
    for t in transitions:
        worker.pub.publish_detection(worker.cfg.module_id, t.camera_id, t.item,
                                     t.state, t.confidence, t.boxes)


def main(argv: list[str]) -> int:
    configure_logging()
    cfg = config_module.load()
    if "--dry-run" in argv:
        cfg.dry_run = True

    make = DryRunSource if cfg.dry_run else YoloSource
    log.info("모듈 %s 기동 (%s)", cfg.module_id,
             "dry-run" if cfg.dry_run else f"추론 · {cfg.model_path}")

    runner = Runner(
        cfg,
        make_source=lambda item: make(cfg, item),
        on_boxes=on_boxes,
        on_stream_end=on_stream_end,
        description="서버에서 RTSP 를 받아 추론하는 사이드카",
    )
    return main_loop(runner)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

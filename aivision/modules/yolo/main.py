"""객체감지 모듈 — RTSP 를 받아 추론한다.

**학습은 여기 없다.** 예전에는 한 컨테이너에서 학습까지 돌렸는데 걷어냈다. 관제 서버는
24시간 떠 있어야 하는데 학습은 몇 시간짜리 배치 작업이고 GPU 와 수십 GB 데이터셋을
요구한다 — 같이 두면 학습 한 번이 추론을 굶긴다. 학습 코드는 `refs/yolov7-training/`
에 참조용으로 남겨 두었고, 학습은 GPU 워크스테이션에서 돌린 뒤 ONNX 로 내보내
이 화면에서 올리면 된다.

그래서 런타임은 onnxruntime 하나다. torch 를 담지 않으니 이미지가 가볍다.

이 모듈이 자기 화면을 갖는 이유는 그대로다 — **사람이 할 일이 있다.** 모델을 올리고,
그 모델이 무엇을 잡는지 확인해 이름을 붙이고, 플랫폼 탐지 항목에 연결하고, 민감도를
맞춘다. 그것을 플랫폼 화면에 넣으면 코어가 YOLO 를 알게 된다(매니페스토 2번).

한 프로세스에 둘을 담는다.
  · 추론 워커   SDK Runner. 배경 스레드에서 돈다. 판정은 MQTT 로 발행한다.
  · API·화면    FastAPI/uvicorn. 메인 스레드에서 돈다.

실행
  python main.py                  모델이 있으면 추론, 없으면 dry-run 으로 내려앉는다
  python main.py --dry-run        모델을 무시하고 합성 박스를 발행 (전 경로 점검용)
  python main.py --no-serve       화면·API 없이 추론만 (예전 사이드카처럼)
"""

from __future__ import annotations

import logging
import random
import sys
import threading
import time
from pathlib import Path
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
        # 저화질이 있으면 그것을 쓴다. 모델 입력이 640 이라 4K 를 풀어 놓고 다시 줄이는
        # 것은 CPU 를 그냥 버리는 일이다. 없으면 원본으로 내려간다.
        url = item.stream_for(prefer_sub=self.cfg.prefer_sub_stream)
        if not url:
            raise RuntimeError("일감에 스트림 주소가 없습니다")
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"스트림을 열 수 없습니다: {url}")
        # 큐를 짧게 둔다. 밀린 프레임을 따라잡느라 지연이 누적되면 안전관리에서 쓸 수 없다.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap = cap
        log.info("카메라 %d 스트림 연결: %s%s", item.camera_id, url,
                 "" if item.rtsp_sub and url == item.rtsp_sub else "  (저화질 없음 — 원본)")

    def close(self) -> None:
        cap, self.cap = self.cap, None
        if cap is not None:
            cap.release()

    def boxes(self, item: WorkItem, stop: threading.Event) -> Iterator[list[dict]]:
        cap, model = self.cap, self.model
        if cap is None or model is None:
            raise RuntimeError("스트림이 열리지 않았습니다")
        min_px = self.cfg.min_box_px
        aliases = self.cfg.aliases

        while not stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError("프레임 읽기 실패")
            h, w = frame.shape[:2]

            live: list[dict] = []
            found: dict[str, list[dict]] = {}
            for det in model.infer(frame):
                if min_px and _short_side_px(det, w, h) < min_px:
                    # 작은 이물질(볼트·나사류)을 무시하고 싶을 때 쓴다. 학습 라벨에서
                    # 빼는 것보다 여기서 거르는 편이 낫다 — 사내 실험에서 라벨을 지우면
                    # 감독이 모순되어 큰 목표의 성능까지 떨어졌다(mAP 0.851 -> 0.737).
                    continue
                box = det.to_box()
                # 사람이 붙인 이름이 있으면 그것을 그린다. 없으면 모델이 들고 온 이름,
                # 그것도 없으면 인덱스다(inference.Model.class_name).
                box["label"] = aliases.get(box["label"], box["label"])
                live.append(box)
                code = model.item_code(det.cls)
                if code:                               # 연결되지 않은 클래스는 박스만 그린다
                    # 이벤트에 붙는 박스도 같은 이름을 쓴다. 예전에는 항목 코드를 넣어
                    # 이벤트 상세창에 'ITEM-001' 이 그려졌다 — 사람이 읽을 것이 아니다.
                    found.setdefault(code, []).append(dict(box))
            self.last_found = found
            yield live

            if stop.wait(self.interval):
                break


def _short_side_px(det, width: int, height: int) -> float:
    """박스의 기하평균 크기(픽셀). 사내 기준이 sqrt(w*h) 였다."""
    bw = max(0.0, det.x2 - det.x1) * width
    bh = max(0.0, det.y2 - det.y1) * height
    return (bw * bh) ** 0.5


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


# ────────────────────────────────────────────────────────────── 기동

def main(argv: list[str]) -> int:
    configure_logging()
    cfg = config_module.load()
    if "--dry-run" in argv:
        cfg.dry_run = True
    serve = "--no-serve" not in argv

    # 리스트에 담는 이유: 화면에서 모델을 올리면 dry-run -> 추론으로 갈아타야 하는데,
    # 이름에 그냥 묶어 두면 Runner 가 들고 있는 람다가 옛 값을 계속 본다.
    make_source = [DryRunSource if cfg.dry_run else YoloSource]
    log.info("모듈 %s 기동 (%s)", cfg.module_id,
             "dry-run" if cfg.dry_run else f"추론 · {cfg.model_path}")

    runner = Runner(
        cfg,
        make_source=lambda item: make_source[0](cfg, item),
        on_boxes=on_boxes,
        on_stream_end=on_stream_end,
        # 플랫폼이 이 주소를 탭으로 감싸 보여 준다. 브라우저가 닿는 주소여야 하므로
        # 컨테이너 이름이 아니라 밖에서 보이는 주소를 넣는다.
        endpoint=cfg.public_url,
        description="서버에서 RTSP 를 받아 객체를 탐지한다",
    )

    if not serve:
        return main_loop(runner)

    # 추론 워커를 배경으로 돌리고 메인 스레드는 화면·API 를 서빙한다.
    # 순서가 중요하다 — 워커가 먼저 떠야 화면이 첫 폴링에서 상태를 볼 수 있다.
    import uvicorn

    import api

    worker = threading.Thread(target=runner.run, name="inference", daemon=True)
    worker.start()

    def reload_workers() -> None:
        """설정이 바뀌었으니 워커를 새 설정으로 다시 띄운다.

        직접 다시 만들지 않고 **멈추기만** 한다. Runner.reconcile 이 죽은 워커를
        다음 폴링에서 되살리는데, 그때 make_source 가 갱신된 cfg 를 읽는다. 여기서
        따로 만들면 워커 생성 경로가 둘이 되고, 일감 목록과 어긋날 수 있다.
        """
        make_source[0] = DryRunSource if cfg.dry_run else YoloSource
        for w in list(runner.workers.values()):
            w.stop()
        log.info("설정이 바뀌어 워커를 다시 띄웁니다 (%d대)", len(runner.workers))

    app = api.create_app(cfg, runner.status, reload_workers)
    log.info("모듈 화면: %s (컨테이너 안에서는 :%d)", cfg.public_url, cfg.serve_port)

    try:
        uvicorn.run(app, host="0.0.0.0", port=cfg.serve_port, log_level="warning")
    finally:
        runner.stop_event.set()
        worker.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

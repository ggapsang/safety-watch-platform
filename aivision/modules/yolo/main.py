"""서버 YOLO 사이드카 — 배관.

이 파일에는 추론이 없다. 플랫폼과 말을 맞추는 부분(등록·일감 수령·발행·heartbeat)만 모아
둔다. 두 번째 모듈이 나오면 이 파일이 그대로 모듈 SDK 가 된다 — 그래서 추론과 섞지 않는다.

모듈 계약 (플랫폼 쪽 api/modules.py 와 짝)
  1. 등록      POST /api/modules
  2. 일감      GET  /api/modules/{id}/work        카메라별 RTSP 주소 + 옵션
  3. 발행      MQTT aivision/detect/{cam}/{module}   이벤트 (전이 순간만)
               MQTT aivision/live/{cam}              라이브 박스 (적재 안 됨)
  4. 생존      POST /api/modules/{id}/heartbeat

판정 결과를 REST 로 돌려주지 않는다는 점이 중요하다. 카메라 엣지든 우리 모듈이든 같은
인바운드 바인딩 문을 지난다(매니페스토 4번). 우리에게 특권 통로는 없다.

실행
  python main.py                  모델이 있으면 추론, 없으면 dry-run 으로 내려앉는다
  python main.py --dry-run        모델을 무시하고 합성 박스를 발행 (전 경로 점검용)
"""

from __future__ import annotations

import json
import logging
import random
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import config as config_module

log = logging.getLogger("yolo")

DETECT_TOPIC = "aivision/detect/{camera_id}/{module_id}"
LIVE_TOPIC = "aivision/live/{camera_id}"


# ────────────────────────────────────────────────────────────── 플랫폼 REST

@dataclass
class WorkItem:
    camera_id: int
    camera_name: str
    rtsp: str
    options: dict


class Platform:
    """플랫폼 REST 호출. 실패를 예외로 올리지 않고 None 을 돌려준다 —
    플랫폼이 재시작 중이어도 모듈은 계속 살아 있어야 한다."""

    def __init__(self, base_url: str, module_id: str, timeout: float = 8.0) -> None:
        self.base = base_url.rstrip("/")
        self.module_id = module_id
        self.timeout = timeout

    def _call(self, method: str, path: str, body: dict | None = None) -> dict | None:
        url = f"{self.base}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            log.warning("%s %s -> HTTP %d %s", method, path, exc.code, detail)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("%s %s -> 연결 실패: %s", method, path, exc)
        except json.JSONDecodeError:
            log.warning("%s %s -> 응답이 JSON 이 아닙니다", method, path)
        return None

    def register(self, name: str, capabilities: list[str]) -> bool:
        body = {"id": self.module_id, "name": name, "kind": "sidecar",
                "description": "서버에서 RTSP 를 받아 추론하는 사이드카",
                "capabilities": capabilities}
        return self._call("POST", "/api/modules", body) is not None

    def work(self) -> list[WorkItem] | None:
        data = self._call("GET", f"/api/modules/{self.module_id}/work")
        if data is None:
            return None
        items = []
        for row in data.get("items", []):
            items.append(WorkItem(camera_id=int(row["camera_id"]),
                                  camera_name=row.get("camera_name") or "",
                                  rtsp=row.get("rtsp") or "",
                                  options=row.get("options") or {}))
        return items

    def heartbeat(self, status: dict) -> None:
        self._call("POST", f"/api/modules/{self.module_id}/heartbeat", status)


# ────────────────────────────────────────────────────────────── MQTT 발행

class Publisher:
    """발행 전용 MQTT 연결.

    paho 의 재연결에 맡긴다(loop_start + reconnect_delay_set). 여기서 큐를 만들지 않는
    이유: 라이브 박스는 늦게 도착하면 의미가 없고, 전이 이벤트는 유실되면 다음 전이가
    있을 뿐이다. 이벤트를 잃지 않는 책임은 플랫폼의 outbox 가 진다.
    """

    def __init__(self, host: str, port: int, client_id: str) -> None:
        import paho.mqtt.client as mqtt

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"{client_id}-pub")
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = lambda *_: log.info("브로커 연결: %s:%d", host, port)
        self.client.on_disconnect = lambda *_: log.warning("브로커 연결 끊김 — 재연결 시도")
        self.host, self.port = host, port

    def start(self) -> None:
        try:
            self.client.connect_async(self.host, self.port, keepalive=60)
            self.client.loop_start()
        except OSError as exc:
            log.warning("브로커 연결 실패(%s) — 계속 재시도합니다", exc)

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def publish(self, topic: str, payload: dict, qos: int = 0) -> None:
        try:
            self.client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=qos)
        except Exception as exc:                                # noqa: BLE001
            log.warning("발행 실패 %s: %s", topic, exc)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ────────────────────────────────────────────────────────────── 카메라 워커

class CameraWorker(threading.Thread):
    """카메라 한 대를 맡는 스레드.

    스레드로 두는 이유: cv2 의 프레임 읽기와 추론이 모두 블로킹이고 GIL 밖에서 도므로,
    asyncio 로 감싸는 것보다 스레드가 정직하다. 모델은 스레드마다 따로 로드한다
    (ONNX 세션을 여러 스레드에서 공유하면 조용히 느려지거나 깨진다).
    """

    def __init__(self, item: WorkItem, cfg, publisher: Publisher) -> None:
        super().__init__(name=f"cam{item.camera_id}", daemon=True)
        self.item = item
        self.cfg = cfg
        self.pub = publisher
        self.stop_event = threading.Event()
        self.frames = 0
        self.detections = 0
        self.last_error = ""
        # 실제로 쓰이는 장치. cfg.device 는 요청값일 뿐이라 CUDA -> CPU 폴백을 숨긴다.
        self.device = "-"
        self._last_live = 0.0

        from debounce import Debouncer

        options = item.options or {}
        self.debouncer = Debouncer(
            hold_sec=float(options.get("hold_sec", cfg.hold_sec)),
            min_conf=float(options.get("min_conf", cfg.min_conf)))
        self.sample_fps = max(0.2, float(options.get("sample_fps", cfg.sample_fps)))

    # ── 발행 ──

    def _emit(self, transitions) -> None:
        for t in transitions:
            self.pub.publish(DETECT_TOPIC.format(camera_id=t.camera_id,
                                                 module_id=self.cfg.module_id), {
                "camera_id": t.camera_id,
                "module_id": self.cfg.module_id,
                "item": t.item,
                "state": t.state,
                "ts": _now_iso(),
                "confidence": round(t.confidence, 3),
                "boxes": t.boxes,
            })
            self.detections += 1

    def _emit_live(self, boxes: list[dict]) -> None:
        if not self.cfg.publish_live:
            return
        now = time.monotonic()
        if now - self._last_live < self.cfg.live_min_interval:
            return
        self._last_live = now
        self.pub.publish(LIVE_TOPIC.format(camera_id=self.item.camera_id),
                         {"camera_id": self.item.camera_id,
                          "module_id": self.cfg.module_id,
                          "ts": _now_iso(), "boxes": boxes})

    # ── 수명주기 ──

    def run(self) -> None:
        log.info("카메라 %d(%s) 워커 시작 — %s", self.item.camera_id,
                 self.item.camera_name, self.item.rtsp or "(주소 없음)")
        try:
            if self.cfg.dry_run:
                self._run_dry()
            else:
                self._run_real()
        except Exception:                                       # noqa: BLE001
            log.exception("카메라 %d 워커가 예외로 종료했습니다", self.item.camera_id)
        finally:
            self._emit(self.debouncer.close_camera(self.item.camera_id, time.monotonic()))
            log.info("카메라 %d 워커 종료 (프레임 %d, 전이 %d)",
                     self.item.camera_id, self.frames, self.detections)

    def stop(self) -> None:
        self.stop_event.set()

    # ── dry-run ──

    def _run_dry(self) -> None:
        """모델 없이 합성 박스를 발행한다.

        영상을 열지 않는다. 카메라가 꺼져 있어도 등록 -> 일감 -> 발행 -> 바인딩 -> 이벤트
        전 경로를 확인할 수 있어야 한다는 것이 dry-run 의 목적이다. 실제 RTSP 는
        _run_real 이 검증한다.
        """
        items = self.cfg.capabilities
        if not items:
            log.error("dry-run 인데 CLASS_MAP 이 비어 있어 발행할 항목이 없습니다. "
                      "플랫폼에서 탐지 항목을 만들고 CLASS_MAP 을 채우세요.")
            return

        interval = 1.0 / self.sample_fps
        phase = 0.0
        while not self.stop_event.wait(interval):
            self.frames += 1
            phase += interval
            # 15초 켜고 10초 끄기를 반복한다. 발생과 해제가 모두 나와야 검증이 된다.
            on = (phase % 25.0) < 15.0
            item = items[int(phase // 25.0) % len(items)]
            boxes = [self._synthetic_box(item, phase)] if on else []
            found = {item: boxes} if on else {}
            self._emit(self.debouncer.observe(self.item.camera_id, time.monotonic(), found))
            self._emit_live(boxes)

    @staticmethod
    def _synthetic_box(label: str, phase: float) -> dict:
        """화면을 천천히 가로지르는 박스. 오버레이가 살아 있는지 눈으로 본다."""
        x = 0.05 + 0.6 * ((phase / 12.0) % 1.0)
        y = 0.25 + 0.1 * random.random()
        return {"x1": round(x, 4), "y1": round(y, 4),
                "x2": round(x + 0.22, 4), "y2": round(y + 0.35, 4),
                "label": label, "score": round(0.72 + 0.2 * random.random(), 3)}

    # ── 실제 추론 ──

    def _run_real(self) -> None:
        import cv2
        import inference

        model = inference.load(self.cfg.model_path, self.cfg.device, self.cfg.imgsz,
                               self.cfg.conf_thres, self.cfg.iou_thres, self.cfg.class_map,
                               self.cfg.layout)
        self.device = model.device
        interval = 1.0 / self.sample_fps
        backoff = 1.0
        cap = None

        try:
            while not self.stop_event.is_set():
                if cap is None or not cap.isOpened():
                    if cap is not None:
                        cap.release()
                        # 스트림이 끊긴 채로 활성이 남으면 화면에 영원히 켜져 있다.
                        self._emit(self.debouncer.close_camera(self.item.camera_id,
                                                               time.monotonic()))
                    cap = self._open(cv2)
                    if cap is None:
                        if self.stop_event.wait(backoff):
                            break
                        backoff = min(backoff * 2, 30.0)
                        continue
                    backoff = 1.0

                ok, frame = cap.read()
                if not ok or frame is None:
                    self.last_error = "프레임 읽기 실패"
                    cap.release()
                    cap = None
                    continue

                self.frames += 1
                found: dict[str, list[dict]] = {}
                live: list[dict] = []
                for det in model.infer(frame):
                    box = det.to_box()
                    live.append(box)
                    code = model.item_code(det.cls)
                    if not code:
                        continue          # CLASS_MAP 에 없는 클래스는 이 현장의 관심 밖
                    box = dict(box, label=code)
                    found.setdefault(code, []).append(box)

                self._emit(self.debouncer.observe(self.item.camera_id,
                                                 time.monotonic(), found))
                self._emit_live(live)
                if self.stop_event.wait(interval):
                    break
        finally:
            if cap is not None:
                cap.release()

    def _open(self, cv2):
        """RTSP 를 연다. 미디어 서버 주소를 그대로 쓴다 — 카메라에 직접 붙지 않는다."""
        url = self.item.rtsp
        if not url:
            self.last_error = "일감에 스트림 주소가 없습니다"
            log.warning("카메라 %d: %s", self.item.camera_id, self.last_error)
            return None
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            self.last_error = "스트림 열기 실패"
            log.warning("카메라 %d: 스트림을 열 수 없습니다 (%s)", self.item.camera_id, url)
            cap.release()
            return None
        # 큐를 짧게 둔다. 밀린 프레임을 따라잡느라 지연이 누적되면 안전관리에서 쓸 수 없다.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.last_error = ""
        log.info("카메라 %d 스트림 연결", self.item.camera_id)
        return cap


# ────────────────────────────────────────────────────────────── 수명주기

class Runner:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.platform = Platform(cfg.platform_url, cfg.module_id)
        self.publisher = Publisher(cfg.mqtt_host, cfg.mqtt_port, cfg.module_id)
        self.workers: dict[int, CameraWorker] = {}
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
            worker = CameraWorker(item, self.cfg, self.publisher)
            self.workers[cam_id] = worker
            worker.start()

    def status(self) -> dict:
        devices = sorted({w.device for w in self.workers.values()})
        return {
            "mode": "dry-run" if self.cfg.dry_run else "inference",
            # 요청값이 아니라 실제 장치를 보고한다. 'cuda 로 띄웠는데 CPU 로 돌고 있다' 를
            # 화면에서 알 수 있어야 한다.
            "device": ", ".join(devices) or "-",
            "cameras": sorted(self.workers),
            "frames": sum(w.frames for w in self.workers.values()),
            "transitions": sum(w.detections for w in self.workers.values()),
            "errors": {str(c): w.last_error for c, w in self.workers.items()
                       if w.last_error},
        }

    def run(self) -> None:
        cfg = self.cfg
        log.info("모듈 %s 기동 (%s)", cfg.module_id,
                 "dry-run" if cfg.dry_run else f"추론 · {cfg.model_path}")
        self.publisher.start()

        while not self.stop_event.is_set():
            if self.platform.register(cfg.module_name, cfg.capabilities):
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
            if now - last_heartbeat >= self.cfg.heartbeat_sec:
                last_heartbeat = now
                self.platform.heartbeat(self.status())

            self.stop_event.wait(self.cfg.work_poll_sec)

        self.shutdown()

    def shutdown(self) -> None:
        log.info("종료 중…")
        for worker in self.workers.values():
            worker.stop()
        for worker in self.workers.values():
            worker.join(timeout=5.0)
        self.publisher.stop()
        log.info("종료 완료")


def main(argv: list[str]) -> int:
    # 설정을 읽기 전에 로깅을 켠다 — 설정 자체에 대한 경고(모델 없음, CLASS_MAP 비어 있음)가
    # 가장 먼저 봐야 하는 것인데, 순서가 뒤바뀌면 그게 묻힌다.
    import os

    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")

    cfg = config_module.load()
    if "--dry-run" in argv:
        cfg.dry_run = True

    runner = Runner(cfg)
    signal.signal(signal.SIGTERM, lambda *_: runner.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: runner.stop_event.set())
    try:
        runner.run()
    except KeyboardInterrupt:
        runner.stop_event.set()
        runner.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

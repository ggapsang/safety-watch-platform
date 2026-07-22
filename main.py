"""아이에스에코솔루션 컨베이어 부적합(쓰레기) 탐지 — PC 추론 앱 진입점.

  [카메라] --RTSP--> [PC] --XGT(TCP 2004)--> [PLC] --> [컨베이어]
     (1) 영상 수신  (2) 전처리  (3) GPU 추론  (4) 후처리
     (5) 판정(2초 window + 9매트릭스 + cooldown)  (6) PLC 제어  (7) 대시보드

사용:
  python main.py                      # .env 설정으로 상시 운전
  python main.py --probe              # 모델만 로드해 출력 shape 진단 후 종료 (M1)
  python main.py --source sample.mp4 --no-plc --show
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

import numpy as np

from app.config import (AppConfig, apply_settings, save_runtime_settings,
                        settings_dict)
from app.dashboard import serve_in_thread
from app.detector import Detector, area_metric, resolve_roi
from app.plc import PlcController
from app.recorder import Recorder
from app.state import SharedState, StateLogHandler, encode_jpeg
from app.trash_logic import (SEVERITY_LABELS, CooldownDecider, FaultTracker,
                             Severity, TrashJudge)
from app.video import VideoSource

log = logging.getLogger("main")
det_log = logging.getLogger("detect")

DETAIL_LOG_INTERVAL = 0.5      # 검출 상세 로그 최소 간격(초) — 20fps 로그 폭주 방지
CAPTURE_WIDTH = 420            # 최근 판독 썸네일 폭

# 오버레이는 cv2.putText 라 한글이 깨진다 -> 화면은 ASCII, 한글은 대시보드에서 표시.
_ASCII_LABELS = {0: "NORMAL", 1: "PARTIAL", 2: "HEAVY", 9: "FAULT"}
_BGR = {0: (113, 191, 47), 1: (49, 185, 232), 2: (77, 72, 229), 9: (77, 72, 229)}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="컨베이어 부적합 탐지 (PC 추론)")
    p.add_argument("--source", help="RTSP URL / 동영상 파일 / 웹캠 인덱스 (VIDEO_SOURCE 덮어쓰기)")
    p.add_argument("--backend", choices=["onnx", "torchscript"], help="추론 백엔드")
    p.add_argument("--device", choices=["cuda", "cpu"], help="추론 디바이스")
    p.add_argument("--no-plc", action="store_true", help="PLC 출력 비활성(로그만)")
    p.add_argument("--no-dashboard", action="store_true", help="대시보드 비활성")
    p.add_argument("--show", action="store_true", help="로컬 창으로 표시(비 Docker 환경)")
    p.add_argument("--probe", action="store_true",
                   help="모델 로드 + 더미 1프레임 추론으로 출력 shape/모드만 진단하고 종료")
    p.add_argument("--log-level", default=None)
    return p.parse_args()


def build_config(args: argparse.Namespace) -> AppConfig:
    cfg = AppConfig.from_env()
    if args.source:
        cfg.video.source = args.source
    if args.backend:
        cfg.model.backend = args.backend
    if args.device:
        cfg.model.device = args.device
    if args.no_plc:
        cfg.plc.enabled = False
    if args.no_dashboard:
        cfg.dashboard.enabled = False
    if args.log_level:
        cfg.log_level = args.log_level.upper()
    cfg.validate()
    return cfg


def draw_overlay(frame: np.ndarray, dets, snap, roi) -> np.ndarray:
    import cv2

    out = frame.copy()
    code = 9 if snap.in_fault else snap.severity
    color = _BGR[code]
    # 4K 원본에서도 읽히도록 프레임 폭에 비례해 글자·선 두께를 키운다.
    k = max(1.0, out.shape[1] / 1280.0)
    th = max(1, int(round(2 * k)))
    font = cv2.FONT_HERSHEY_SIMPLEX

    for d in dets:
        cv2.rectangle(out, (int(d.x1), int(d.y1)), (int(d.x2), int(d.y2)), (0, 200, 255), th)
        cv2.putText(out, f"trash {d.score:.2f}", (int(d.x1), max(int(14 * k), int(d.y1) - int(8 * k))),
                    font, 0.5 * k, (0, 200, 255), max(1, th // 2), cv2.LINE_AA)
    if roi:
        pts = np.array([[int(x), int(y)] for x, y in roi], dtype=np.int32)
        cv2.polylines(out, [pts], True, (200, 200, 200), th)

    bar = int(62 * k)
    cv2.rectangle(out, (0, 0), (out.shape[1], bar), (20, 22, 26), -1)
    cv2.putText(out, f"{code} {_ASCII_LABELS[code]}", (int(14 * k), int(30 * k)),
                font, 0.9 * k, color, th, cv2.LINE_AA)
    unit = "%" if snap.area_unit == "percent" else ""
    cv2.putText(out,
                f"cat={snap.category}  max_cnt={snap.max_count}  max_area={snap.max_area:.3f}{unit}"
                f"  det={snap.det_count}  {snap.fps:.1f}fps  {snap.infer_ms:.0f}ms",
                (int(14 * k), int(52 * k)), font, 0.5 * k, (220, 226, 235),
                max(1, th // 2), cv2.LINE_AA)
    return out


def probe(cfg: AppConfig) -> int:
    """모델만 로드해 더미 프레임으로 출력 형태를 진단한다 (M1 착수 확인용)."""
    det = Detector(cfg)
    dummy = np.zeros((cfg.model.imgsz, cfg.model.imgsz, 3), dtype=np.uint8)
    dets = det.detect(dummy)
    log.info("[probe] 백엔드=%s device=%s infer_mode=%s 추론시간=%.1fms 검출=%d",
             cfg.model.backend, det.backend.device, det.infer_mode, det.last_infer_ms, len(dets))
    return 0


def _detail_lines(dets, detector) -> list[str]:
    """검출 상세 — 실제 conf 와 '후처리 전에 어떤 물체로 봤는지'."""
    lines = []
    for i, d in enumerate(sorted(dets, key=lambda x: -x.score)[:3], start=1):
        raw = ""
        if d.raw_scores:
            per = " ".join(f"{detector.class_name(k)}={v:.3f}"
                           for k, v in enumerate(d.raw_scores))
            raw = f" | 후처리전={d.raw_label} [{per}]"
        lines.append(f"#{i} conf={d.score:.3f} (obj={d.obj:.3f} cls={d.cls:.3f})"
                     f" box=({d.x1:.0f},{d.y1:.0f})-({d.x2:.0f},{d.y2:.0f}){raw}")
    return lines


def run(cfg: AppConfig, show: bool) -> int:
    state = SharedState()
    logging.getLogger().addHandler(StateLogHandler(state))
    detector = Detector(cfg)
    state.update(backend=cfg.model.backend, device=detector.backend.device,
                 infer_mode=detector.infer_mode, plc_enabled=cfg.plc.enabled,
                 area_unit=cfg.logic.area_unit, running=True)

    judge = TrashJudge(cfg.logic.window_sec, cfg.logic.count_low, cfg.logic.count_high,
                       cfg.logic.area_low, cfg.logic.area_high)
    decider = CooldownDecider(cfg.logic.cooldown_sec, cfg.logic.write_zero_on_normal)
    fault = FaultTracker(cfg.logic.fault_after_sec, cfg.logic.fault_code)
    plc = PlcController(cfg)
    plc.startup()

    # 대시보드에서 온 설정 변경은 여기 모아두고, 추론 루프가 안전한 시점에 반영한다
    # (영상/PLC 객체는 추론 루프 소유 — 다른 스레드에서 만지면 안 된다).
    pending: set[str] = set()
    pending_lock = threading.Lock()

    def on_settings(patch: dict) -> set[str]:
        changed = apply_settings(cfg, patch)
        if changed:
            # 저장 파일에는 실제 비밀번호를 남긴다(마스킹은 HTTP 응답에만 적용).
            # 마스킹된 값을 저장하면 UI 로 바꾼 비밀번호가 재시작 때 사라진다.
            save_runtime_settings(settings_dict(cfg, reveal_password=True))
            with pending_lock:
                pending.update(changed)
            log.info("설정 변경 적용: %s", ", ".join(sorted(changed)))
        return changed

    recorder = Recorder(cfg.dashboard.recordings_dir)

    if cfg.dashboard.enabled:
        serve_in_thread(state, cfg, on_settings, recorder)

    stopping = {"flag": False}

    def _stop(signum, _frame) -> None:
        log.info("종료 신호(%s) 수신 — 정리 중", signum)
        stopping["flag"] = True

    signal.signal(signal.SIGINT, _stop)
    try:
        signal.signal(signal.SIGTERM, _stop)
    except (AttributeError, ValueError):
        pass

    video = VideoSource(cfg.video.source, cfg.video.reconnect_sec,
                        cfg.video.read_timeout_sec, cfg.video.ffmpeg_options).start()

    last_id = -1
    fps = 0.0
    last_t = time.perf_counter()
    roi_cache: list[tuple[float, float]] = []
    frame_shape: tuple[int, int] | None = None
    # 프레임이 이 시간 이상 안 들어오면 '영상 끊김'으로 본다. 그 상태가
    # FAULT_AFTER_SEC 이상 지속되면 FaultTracker 가 코드 9 를 1회 전송한다.
    STALE_SEC = 2.0

    def track_health(healthy: bool) -> None:
        """판정실패(코드 9) 진입/복구 처리."""
        fd = fault.update(healthy)
        if fd.send:
            log.error("판정실패 — %s", fd.reason)
            if plc.write_result(fd.code):
                state.update(last_sent_code=fd.code, last_sent_at=time.time())
        elif fd.reason == "판정실패 복구":
            log.info("판정실패 복구 — 판정 재개(다음 판정을 즉시 전송)")
            judge.window.clear()
            decider.reset(force_next_send=True)
        state.update(in_fault=fault.in_fault, fault_reason=fd.reason)

    last_detail_at = 0.0

    def apply_pending() -> None:
        nonlocal video, last_id, frame_shape
        with pending_lock:
            if not pending:
                return
            todo = set(pending)
            pending.clear()
        if "judge" in todo:
            judge.count_low = cfg.logic.count_low
            judge.count_high = cfg.logic.count_high
            judge.area_low = cfg.logic.area_low
            judge.area_high = cfg.logic.area_high
            decider.cooldown_sec = max(1.0, cfg.logic.cooldown_sec)
            log.info("판정 기준 갱신: conf=%.2f count(%d/%d) area(%.2f/%.2f) cooldown=%.0fs",
                     cfg.model.conf_threshold, cfg.logic.count_low, cfg.logic.count_high,
                     cfg.logic.area_low, cfg.logic.area_high, decider.cooldown_sec)
        if "plc" in todo:
            plc.reconfigure(cfg)
        if "camera" in todo:
            log.info("영상 소스 재연결")
            video.stop()
            video = VideoSource(cfg.video.source, cfg.video.reconnect_sec,
                                cfg.video.read_timeout_sec, cfg.video.ffmpeg_options).start()
            last_id = -1
            frame_shape = None

    try:
        while not stopping["flag"]:
            apply_pending()
            frame_id, frame = video.read_latest(last_id)
            if frame is None:
                track_health(video.connected and video.stale_sec < STALE_SEC)
                state.update(video_connected=video.connected, video_reconnects=video.reconnects,
                             plc_connected=plc.connected, plc_last_write=plc.last_write,
                             plc_last_error=plc.last_error, plc_write_ok=plc.write_ok,
                             plc_write_fail=plc.write_fail)
                time.sleep(0.02)
                continue
            last_id = frame_id

            h, w = frame.shape[:2]
            if frame_shape != (h, w):
                frame_shape = (h, w)
                roi_cache = resolve_roi(cfg.logic.roi_polygon, w, h)
                log.info("프레임 %dx%d, ROI 꼭짓점 %d개", w, h, len(roi_cache))

            try:
                dets = detector.detect(frame)
            except Exception:
                log.exception("추론 실패")
                track_health(False)
                continue
            track_health(True)

            count = len(dets)
            area = area_metric(dets, w, h, roi_cache, cfg.logic.area_unit)

            now_m = time.monotonic()
            if dets and now_m - last_detail_at >= DETAIL_LOG_INTERVAL:
                last_detail_at = now_m
                for line in _detail_lines(dets, detector):
                    det_log.info(line)

            j = judge.update(count, area)
            decision = decider.decide(j)
            capture_meta = None
            if decision.send:
                if plc.write_result(decision.code):
                    state.update(last_sent_code=decision.code, last_sent_at=time.time())
                log.info("판정 %s severity=%d code=%d (%s) max_cnt=%d max_area=%.3f",
                         j.category, int(j.severity), decision.code, decision.reason,
                         j.max_count, j.max_area)
                top = max(dets, key=lambda d: d.score) if dets else None
                capture_meta = {
                    "code": decision.code,
                    "label": SEVERITY_LABELS.get(Severity(int(j.severity)), "-"),
                    "category": j.category,
                    "det_count": count,
                    "top_score": float(top.score) if top else 0.0,
                    "raw_label": top.raw_label if top else "",
                }

            now = time.perf_counter()
            dt = now - last_t
            last_t = now
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps else 1.0 / dt

            state.update(
                video_connected=video.connected, video_reconnects=video.reconnects,
                plc_connected=plc.connected, plc_last_write=plc.last_write,
                plc_last_error=plc.last_error, plc_write_ok=plc.write_ok,
                plc_write_fail=plc.write_fail,
                fps=fps, infer_ms=detector.last_infer_ms, det_count=count, area_value=area,
                max_count=j.max_count, max_area=j.max_area, category=j.category,
                severity=int(j.severity), severity_label=SEVERITY_LABELS[Severity(int(j.severity))],
                decision_reason=decision.reason,
            )

            if recorder.active and not recorder.overlay:
                recorder.write(frame)                # 오버레이 없이 원본 그대로

            if cfg.dashboard.enabled or show or (recorder.active and recorder.overlay):
                canvas = draw_overlay(frame, dets, state.snapshot(), roi_cache)
                if recorder.active and recorder.overlay:
                    recorder.write(canvas)
                if cfg.dashboard.enabled:
                    jpeg = encode_jpeg(canvas, cfg.dashboard.jpeg_quality)
                    if jpeg:
                        state.set_jpeg(jpeg)
                    if capture_meta is not None:
                        thumb = encode_jpeg(canvas, 70, max_width=CAPTURE_WIDTH)
                        if thumb:
                            state.add_capture(thumb, **capture_meta)
                if show:
                    import cv2

                    cv2.imshow("trash detector", canvas)
                    if cv2.waitKey(1) & 0xFF == 27:
                        stopping["flag"] = True
    finally:
        state.update(running=False)
        if recorder.active:
            recorder.stop("앱 종료")
        video.stop()
        plc.shutdown()
        if show:
            try:
                import cv2

                cv2.destroyAllWindows()
            except Exception:
                pass
        log.info("종료 완료")
    return 0


def main() -> int:
    args = parse_args()
    cfg = build_config(args)
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log.info("모델: %s (backend=%s, device=%s, infer_mode=%s)",
             cfg.model_path(), cfg.model.backend, cfg.model.device, cfg.model.infer_mode)

    if args.probe:
        return probe(cfg)
    if not cfg.video.source:
        log.error("VIDEO_SOURCE 가 없습니다. .env 를 채우거나 --source 를 지정하세요.")
        return 2
    return run(cfg, args.show)


if __name__ == "__main__":
    sys.exit(main())

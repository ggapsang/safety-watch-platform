"""자체 검증 테스트 — 외부 의존성(cv2/onnxruntime/torch) 없이 numpy 만으로 돌아간다.

  python -m unittest discover -s tests -v      (또는 pytest tests)

검증 대상:
  1. XGT 프레임 바이트 (헤더 20byte, BCC, Read/Write body 레이아웃)
  2. 판정 로직 분류 경계값 (07-01 개정: count 상한 포함 / area 상한 미포함)
  3. cooldown 판단 (L_L skip, 카테고리 변경 우회, 동일 카테고리 억제)
  4. 후처리 유틸 (xywh2xyxy, NMS, point-in-polygon, Shoelace, 좌표 역변환)
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import detector as D                                     # noqa: E402
from app import xgt_client as X                                   # noqa: E402
from app.trash_logic import (CooldownDecider, FaultTracker, Level,  # noqa: E402
                             Severity, TrashJudge, classify_area, classify_count)


class TestXgtFrame(unittest.TestCase):
    def test_header_layout(self):
        h = X.build_header(invoke_id=1, body_len=16, cpu_info=0x00)
        self.assertEqual(len(h), 20)
        self.assertEqual(h[0:10], b"LSIS-XGT\x00\x00")
        self.assertEqual(struct.unpack_from("<H", h, 10)[0], 0x0000)   # PLC Info
        self.assertEqual(h[12], 0x00)                                  # CPU_ANY
        self.assertEqual(h[13], 0x33)                                  # client -> server
        self.assertEqual(struct.unpack_from("<H", h, 14)[0], 1)        # invoke id
        self.assertEqual(struct.unpack_from("<H", h, 16)[0], 16)       # body length
        self.assertEqual(h[18], 0x00)                                  # position
        self.assertEqual(h[19], sum(h[:19]) & 0xFF)                    # BCC

    def test_write_body_bytes(self):
        body = X.build_write_body(["%DW8101"], [2])
        self.assertEqual(body[0:2], b"\x58\x00")     # Write command
        self.assertEqual(body[2:4], b"\x02\x00")     # Word data type
        self.assertEqual(struct.unpack_from("<H", body, 4)[0], 0)      # reserved
        self.assertEqual(struct.unpack_from("<H", body, 6)[0], 1)      # block count
        self.assertEqual(struct.unpack_from("<H", body, 8)[0], 7)      # len("%DW8101")
        self.assertEqual(body[10:17], b"%DW8101")
        self.assertEqual(struct.unpack_from("<H", body, 17)[0], 2)     # data size = word
        self.assertEqual(struct.unpack_from("<H", body, 19)[0], 2)     # value = 2 (LE)
        self.assertEqual(len(body), 21)

    def test_read_body_bytes(self):
        body = X.build_read_body(["%DW8000", "%DW8001"])
        self.assertEqual(body[0:2], b"\x54\x00")     # Read command
        self.assertEqual(body[2:4], b"\x02\x00")
        self.assertEqual(struct.unpack_from("<H", body, 6)[0], 2)      # 2 blocks
        self.assertEqual(body[10:17], b"%DW8000")
        self.assertEqual(body[19:26], b"%DW8001")

    def test_invoke_id_increments(self):
        c = X.XgtClient("127.0.0.1")
        self.assertEqual([c._next_invoke_id() for _ in range(3)], [1, 2, 3])


class TestClassification(unittest.TestCase):
    """count: < low -> L / >= high -> H / 그 외 M    (상한 경계 포함)
       area : < low -> L / >  high -> H / 그 외 M    (상한 경계 미포함)"""

    def test_count_boundaries(self):
        self.assertEqual(classify_count(0, 1, 2), Level.L)
        self.assertEqual(classify_count(1, 1, 2), Level.M)    # low 경계 -> M
        self.assertEqual(classify_count(2, 1, 2), Level.H)    # high 경계 포함 -> H
        self.assertEqual(classify_count(9, 1, 2), Level.H)

    def test_area_boundaries(self):
        self.assertEqual(classify_area(0.29, 0.3, 3.0), Level.L)
        self.assertEqual(classify_area(0.30, 0.3, 3.0), Level.M)   # low 경계 -> M
        self.assertEqual(classify_area(3.00, 0.3, 3.0), Level.M)   # high 경계 미포함 -> M
        self.assertEqual(classify_area(3.01, 0.3, 3.0), Level.H)


class TestJudge(unittest.TestCase):
    def test_window_holds_max_for_2s(self):
        j = TrashJudge(window_sec=2.0, count_low=1, count_high=2, area_low=0.3, area_high=3.0)
        r = j.update(5, 0.0, now=100.0)
        self.assertEqual((r.max_count, r.category, r.severity), (5, "H_L", Severity.HEAVY))
        r = j.update(0, 0.0, now=101.5)                    # 아직 윈도우 안 -> max 유지
        self.assertEqual(r.max_count, 5)
        r = j.update(0, 0.0, now=102.6)                    # 윈도우 밖 -> 빠짐
        self.assertEqual((r.max_count, r.category, r.severity), (0, "L_L", Severity.NORMAL))

    def test_severity_is_max_of_two_levels(self):
        j = TrashJudge(window_sec=2.0)
        r = j.update(1, 5.0, now=10.0)                     # count=M, area=H
        self.assertEqual((r.count_level, r.area_level), (Level.M, Level.H))
        self.assertEqual((r.category, r.severity), ("M_H", Severity.HEAVY))


class TestCooldown(unittest.TestCase):
    def _j(self, count, area, now):
        return TrashJudge(window_sec=0.001).update(count, area, now=now)

    def test_normal_is_skipped(self):
        d = CooldownDecider(cooldown_sec=8.0)
        self.assertFalse(d.decide(self._j(0, 0.0, 0.0), now=0.0).send)

    def test_same_category_suppressed_then_resent(self):
        d = CooldownDecider(cooldown_sec=8.0)
        self.assertTrue(d.decide(self._j(3, 0.0, 0.0), now=0.0).send)      # H_L 최초
        self.assertFalse(d.decide(self._j(3, 0.0, 1.0), now=1.0).send)     # cooldown 중
        r = d.decide(self._j(3, 0.0, 8.0), now=8.0)                        # cooldown 만료
        self.assertTrue(r.send)
        self.assertEqual(r.code, 2)

    def test_category_change_bypasses_cooldown(self):
        d = CooldownDecider(cooldown_sec=8.0)
        self.assertTrue(d.decide(self._j(1, 0.0, 0.0), now=0.0).send)      # M_L
        r = d.decide(self._j(3, 0.0, 1.0), now=1.0)                        # H_L 로 변경
        self.assertTrue(r.send)
        self.assertEqual(r.code, 2)

    def test_write_zero_on_normal_fires_once(self):
        d = CooldownDecider(cooldown_sec=8.0, write_zero_on_normal=True)
        d.decide(self._j(3, 0.0, 0.0), now=0.0)                            # 오염 발생
        r = d.decide(self._j(0, 0.0, 1.0), now=1.0)                        # 정상 복귀
        self.assertTrue(r.send)
        self.assertEqual(r.code, 0)
        self.assertFalse(d.decide(self._j(0, 0.0, 2.0), now=2.0).send)     # 반복 안 함

    def test_startup_normal_does_not_write_zero(self):
        d = CooldownDecider(cooldown_sec=8.0, write_zero_on_normal=True)
        self.assertFalse(d.decide(self._j(0, 0.0, 0.0), now=0.0).send)


class TestFaultCode9(unittest.TestCase):
    """IF 맵 Rev.2026.07.03 — 9 = 판정실패(카메라 오류) -> 알람."""

    def test_no_fault_before_threshold(self):
        f = FaultTracker(fault_after_sec=5.0)
        self.assertFalse(f.update(False, now=0.0).send)
        self.assertFalse(f.update(False, now=4.9).send)
        self.assertFalse(f.in_fault)

    def test_fires_once_at_threshold_and_does_not_repeat(self):
        f = FaultTracker(fault_after_sec=5.0)
        f.update(False, now=0.0)
        r = f.update(False, now=5.0)
        self.assertTrue(r.send)
        self.assertEqual(r.code, 9)
        self.assertTrue(f.in_fault)
        self.assertFalse(f.update(False, now=6.0).send)      # 알람 코드 — 재전송 안 함
        self.assertFalse(f.update(False, now=60.0).send)

    def test_recovery_reports_and_clears(self):
        f = FaultTracker(fault_after_sec=5.0)
        f.update(False, now=0.0)
        f.update(False, now=5.0)
        r = f.update(True, now=7.0)
        self.assertFalse(r.send)
        self.assertEqual(r.reason, "판정실패 복구")
        self.assertFalse(f.in_fault)

    def test_transient_glitch_does_not_trip(self):
        f = FaultTracker(fault_after_sec=5.0)
        f.update(False, now=0.0)
        f.update(True, now=1.0)                              # 짧은 끊김 -> 타이머 리셋
        f.update(False, now=2.0)
        self.assertFalse(f.update(False, now=6.0).send)       # 2.0 기준 4초밖에 안 됨
        self.assertTrue(f.update(False, now=7.0).send)

    def test_reset_force_next_send_writes_zero_after_fault(self):
        """9 를 보낸 뒤 복구되면 라인을 되살릴 0 을 다시 써야 한다."""
        d = CooldownDecider(cooldown_sec=8.0, write_zero_on_normal=True)
        d.decide(TrashJudge(window_sec=0.001).update(0, 0.0, now=0.0), now=0.0)   # 기동 시 정상
        d.reset(force_next_send=True)                                            # 코드 9 전송 후 복구
        r = d.decide(TrashJudge(window_sec=0.001).update(0, 0.0, now=1.0), now=1.0)
        self.assertTrue(r.send)
        self.assertEqual(r.code, 0)


class TestPostprocess(unittest.TestCase):
    def test_xywh2xyxy(self):
        out = D.xywh2xyxy(np.array([[10.0, 10.0, 4.0, 6.0]]))
        np.testing.assert_allclose(out, [[8.0, 7.0, 12.0, 13.0]])

    def test_nms_removes_overlap_keeps_distinct(self):
        boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=np.float32)
        scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)
        self.assertEqual(sorted(D.nms(boxes, scores, 0.45)), [0, 2])

    def test_point_in_polygon_and_area(self):
        poly = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        self.assertTrue(D.point_in_polygon(5.0, 5.0, poly))
        self.assertFalse(D.point_in_polygon(15.0, 5.0, poly))
        self.assertAlmostEqual(D.polygon_area(poly), 100.0)

    def test_resolve_roi_normalized_vs_pixel(self):
        norm = D.resolve_roi([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)], 1920, 1080)
        self.assertEqual(norm[1], (1920.0, 1080.0 * 0.0))
        px = D.resolve_roi([(0.0, 0.0), (100.0, 0.0), (100.0, 50.0)], 1920, 1080)
        self.assertEqual(px[1], (100.0, 0.0))

    def test_letterbox_inverse_recovers_original_box(self):
        """1920x1080 -> 640 letterbox 좌표를 역변환하면 원본 좌표로 복원되어야 한다."""
        w, h = 1920, 1080
        ratio = min(640 / h, 640 / w)
        pad_x = (640 - round(w * ratio)) / 2.0
        pad_y = (640 - round(h * ratio)) / 2.0
        orig = np.array([[100.0, 200.0, 500.0, 700.0]])
        boxed = orig * ratio
        boxed[:, [0, 2]] += pad_x
        boxed[:, [1, 3]] += pad_y

        class _Cfg:
            class model:
                letterbox = True
            class logic:
                roi_polygon: list = []

        recovered = D.Detector._scale_back(_Cfg(), boxed.copy(),
                                           ("letterbox", ratio, (pad_x, pad_y)), w, h)
        np.testing.assert_allclose(recovered, orig, atol=1e-6)

    def test_decode_raw_shape_and_range(self):
        pred = np.zeros((25200, 6), dtype=np.float32)
        out = D.decode_raw(pred, imgsz=640)
        self.assertEqual(out.shape, (25200, 6))
        # sigmoid(0)=0.5 -> cx = (0.5*2-0.5+grid)*stride = (0.5+grid)*stride, 첫 셀은 4.0
        self.assertAlmostEqual(float(out[0, 0]), 4.0, places=4)
        self.assertTrue(np.all(out[:, 4] == 0.5))

    def test_area_metric_percent_vs_ratio(self):
        dets = [D.Detection(0, 0, 100, 100, 0.9)]           # 10000 px^2
        pct = D.area_metric(dets, 1000, 1000, [], "percent")
        self.assertAlmostEqual(pct, 1.0)                    # 10000 / 1e6 = 1%
        ratio = D.area_metric(dets, 1000, 1000, [], "ratio")
        self.assertAlmostEqual(ratio, 0.01)

    def test_area_metric_uses_roi_area_as_denominator(self):
        dets = [D.Detection(0, 0, 100, 100, 0.9)]
        roi = [(0.0, 0.0), (500.0, 0.0), (500.0, 500.0), (0.0, 500.0)]   # 250000 px^2
        self.assertAlmostEqual(D.area_metric(dets, 1000, 1000, roi, "percent"), 4.0)


class TestRtspAndSettings(unittest.TestCase):
    def test_rtsp_roundtrip(self):
        from app.config import build_rtsp, parse_rtsp

        url = "rtsp://admin:p%40ss@192.168.5.52/profile2/media.smp"
        d = parse_rtsp(url)
        self.assertEqual((d["ip"], d["user"], d["password"], d["path"]),
                         ("192.168.5.52", "admin", "p@ss", "/profile2/media.smp"))
        self.assertEqual(build_rtsp(d), url)

    def test_rtsp_nonstandard_port_and_no_credentials(self):
        from app.config import build_rtsp, parse_rtsp

        url = build_rtsp({"ip": "10.0.0.5", "port": 5554, "user": "", "password": "",
                          "path": "/live"})
        self.assertEqual(url, "rtsp://10.0.0.5:5554/live")
        self.assertEqual(parse_rtsp(url)["port"], 5554)

    def _cfg(self):
        from app.config import AppConfig

        cfg = AppConfig()
        cfg.video.source = "rtsp://admin:secret@192.168.5.52/profile2/media.smp"
        return cfg

    def test_apply_settings_reports_changed_domains(self):
        from app.config import apply_settings

        cfg = self._cfg()
        changed = apply_settings(cfg, {
            "camera": {"ip": "192.168.5.60"},
            "plc": {"host": "192.168.5.199", "port": 2004},
            "judge": {"conf": 0.4, "cooldown": 12},
        })
        self.assertEqual(changed, {"camera", "judge"})       # plc 는 기본값과 동일 -> 변화 없음
        self.assertIn("192.168.5.60", cfg.video.source)
        self.assertEqual(cfg.model.conf_threshold, 0.4)
        self.assertEqual(cfg.logic.cooldown_sec, 12)

    def test_empty_password_keeps_existing(self):
        from app.config import apply_settings, parse_rtsp

        cfg = self._cfg()
        apply_settings(cfg, {"camera": {"ip": "192.168.5.60", "password": "", "has_password": True}})
        self.assertEqual(parse_rtsp(cfg.video.source)["password"], "secret")

    def test_settings_dict_hides_password_by_default(self):
        from app.config import settings_dict

        cfg = self._cfg()
        s = settings_dict(cfg)
        self.assertEqual(s["camera"]["password"], "")
        self.assertTrue(s["camera"]["has_password"])
        self.assertEqual(settings_dict(cfg, reveal_password=True)["camera"]["password"], "secret")

    def test_cooldown_minimum_is_enforced(self):
        from app.config import apply_settings

        cfg = self._cfg()
        apply_settings(cfg, {"judge": {"cooldown": 0}})
        self.assertEqual(cfg.logic.cooldown_sec, 1.0)         # validate() 가 최소 1초로 올린다

    def test_invalid_thresholds_rejected(self):
        from app.config import apply_settings

        cfg = self._cfg()
        with self.assertRaises(ValueError):
            apply_settings(cfg, {"judge": {"count_low": 5, "count_high": 2}})


class TestStateBuffers(unittest.TestCase):
    def test_log_ring_is_incremental(self):
        from app.state import SharedState

        s = SharedState()
        for i in range(3):
            s.add_log("INFO", f"line{i}")
        rows = s.logs_since(0)
        self.assertEqual([r["msg"] for r in rows], ["line0", "line1", "line2"])
        self.assertEqual([r["msg"] for r in s.logs_since(rows[1]["seq"])], ["line2"])

    def test_captures_keep_only_latest_eight(self):
        from app.state import MAX_CAPTURES, SharedState

        s = SharedState()
        for i in range(MAX_CAPTURES + 4):
            s.add_capture(b"jpeg%d" % i, code=1, label="부분오염", category="M_L",
                          det_count=1, top_score=0.5, raw_label="cls0")
        lst = s.capture_list()
        self.assertEqual(len(lst), MAX_CAPTURES)
        self.assertEqual(lst[0]["id"], MAX_CAPTURES + 4)      # 최신순
        self.assertNotIn("jpeg", lst[0])                      # 목록에는 바이트 미포함
        self.assertEqual(s.capture_jpeg(lst[0]["id"]), b"jpeg11")
        self.assertIsNone(s.capture_jpeg(1))                  # 밀려난 항목


class TestRecorder(unittest.TestCase):
    def test_path_traversal_is_blocked(self):
        import tempfile

        from app.recorder import Recorder

        with tempfile.TemporaryDirectory() as d:
            r = Recorder(Path(d))
            for bad in ("../etc/passwd", "rec_../x.mp4", "notrec.mp4", "rec_x.txt",
                        "sub/rec_a.mp4"):
                self.assertIsNone(r.file_path(bad), bad)
            self.assertIsNone(r.file_path("rec_missing.mp4"))  # 형식은 맞지만 없는 파일

    def test_limit_is_capped_at_60s(self):
        import tempfile

        from app.recorder import MAX_SECONDS_CAP, Recorder

        with tempfile.TemporaryDirectory() as d:
            r = Recorder(Path(d))
            st = r.start(seconds=600)
            self.assertEqual(st["limit"], MAX_SECONDS_CAP)
            with self.assertRaises(ValueError):
                r.start()                                     # 중복 시작 거부
            r.stop()
            self.assertFalse(r.active)


if __name__ == "__main__":
    unittest.main(verbosity=2)

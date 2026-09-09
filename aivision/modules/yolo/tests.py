"""모듈 자체 검증 — 브로커도 모델도 카메라도 없이 돈다.

여기서 확인하는 것 넷.
  1. 출력 모양 판별      모델마다 다른 텐서 모양을 런타임에 옳게 가르는가
  2. 좌표 되돌리기        letterbox 를 거친 박스가 원래 자리로 돌아오는가
  3. 전이 검출            발생은 즉시, 해제는 hold_sec 뒤에 나오는가
  4. 계약 일치            우리가 만든 박스를 플랫폼의 mapping 이 그대로 읽는가

4번이 이 파일의 존재 이유다. 모듈과 플랫폼이 같은 저장소에 있어도 서로 다른 프로세스라
계약이 어긋나도 조용하다. 조용히 어긋나는 것을 여기서 잡는다.

실행: python tests.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # _sdk 가 옆에 있다

import inference                                                  # noqa: E402
from _sdk import Debouncer                                        # noqa: E402

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


# ────────────────────────────────────────────────── 1. 출력 모양 판별

def test_output_shapes() -> None:
    print("출력 모양 판별")

    # yolov5/v7: (1, N, 5+nc) - cx cy w h obj cls...  (N = 후보 수, 실제 모델과 같은 크기)
    v5 = np.zeros((1, 25200, 7), dtype=np.float32)
    v5[0, 0] = [100, 100, 40, 60, 0.9, 0.8, 0.1]      # cls 0
    v5[0, 1] = [300, 200, 20, 20, 0.8, 0.2, 0.7]      # cls 1
    v5[0, 2] = [10, 10, 4, 4, 0.01, 0.5, 0.5]         # 낮은 obj
    boxes, scores, classes = inference._as_candidates(v5)
    check(list(classes[:3]) == [0, 1, 0], "v5 클래스 선택")
    check(abs(scores[0] - 0.72) < 1e-5, "v5 점수 = obj * cls")
    check(abs(boxes[0][0] - 80) < 1e-4 and abs(boxes[0][2] - 120) < 1e-4,
          "v5 cxcywh -> xyxy")

    # yolov8: (1, 4+nc, N) - 전치되어 나오고 obj 가 없다
    v8 = np.zeros((1, 6, 8400), dtype=np.float32)     # 4 + 2 클래스, 후보 8400
    v8[0, :, 0] = [100, 100, 40, 60, 0.9, 0.1]
    v8[0, :, 1] = [300, 200, 20, 20, 0.2, 0.75]
    boxes, scores, classes = inference._as_candidates(v8)
    check(boxes.shape[0] == 8400, "v8 전치 판별")
    check(classes[0] == 0 and classes[1] == 1, "v8 클래스 선택")
    check(abs(scores[1] - 0.75) < 1e-5, "v8 점수에 obj 를 곱하지 않음")

    # 이미 NMS 까지 된 export: (1, N, 6)
    done = np.array([[[10, 20, 50, 80, 0.66, 2]]], dtype=np.float32)
    boxes, scores, classes = inference._as_candidates(done)
    check(list(boxes[0]) == [10, 20, 50, 80], "NMS 완료 출력은 좌표를 건드리지 않음")
    check(classes[0] == 2 and abs(scores[0] - 0.66) < 1e-6, "NMS 완료 출력의 점수·클래스")

    # 애매하게 작은 텐서를 함부로 전치하지 않는다 (전치하면 좌표가 뒤집힌다)
    small = np.zeros((1, 3, 7), dtype=np.float32)
    small[0, 0] = [100, 100, 40, 60, 0.9, 0.8, 0.1]
    boxes, _, _ = inference._as_candidates(small)
    check(boxes.shape[0] == 3, "작고 애매한 텐서는 전치하지 않는다")

    # 클래스 수를 알면 열 수로 확정된다 (추측 없음). 2클래스 v8 은 전치 후 6열이 되어
    # NMS 완료 출력과 열 수가 같아지는데, 이때 잘못 갈라지면 점수와 클래스가 뒤바뀐다.
    boxes, scores, classes = inference._as_candidates(v8, nc=2)
    check(classes[1] == 1 and abs(scores[1] - 0.75) < 1e-5, "클래스 수를 알 때 v8 확정")
    v5_1cls = np.zeros((1, 25200, 6), dtype=np.float32)
    v5_1cls[0, 0] = [100, 100, 40, 60, 0.9, 0.8]
    _, scores, classes = inference._as_candidates(v5_1cls, nc=1)
    check(abs(scores[0] - 0.72) < 1e-5 and classes[0] == 0,
          "1클래스 v5 의 6열 출력을 NMS 결과로 오해하지 않는다")

    # 자동 판별이 틀리는 모델을 만나면 사람이 못 박을 수 있어야 한다
    _, scores, _ = inference._as_candidates(v5[:, :3, :], layout="v5")
    check(abs(scores[0] - 0.72) < 1e-5, "LAYOUT=v5 지정")
    _, scores, classes = inference._as_candidates(v5[:, :3, :], layout="v8")
    check(classes[0] == 0 and abs(scores[0] - 0.9) < 1e-5,
          "LAYOUT=v8 지정 (5번째 열을 obj 가 아니라 클래스 점수로 읽는다)")

    try:
        inference._as_candidates(np.zeros((1, 5, 3), dtype=np.float32))
    except ValueError:
        check(True, "다룰 수 없는 모양은 예외로 알린다")
    else:
        check(False, "다룰 수 없는 모양은 예외로 알린다")


# ────────────────────────────────────────────────── 2. 좌표 되돌리기

def test_scale_back() -> None:
    print("좌표 되돌리기")
    # 가로가 긴 프레임 - letterbox 가 위아래에 띠를 넣는 경우
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    img, ratio, pad = inference.letterbox(frame, 640)
    check(img.shape[:2] == (640, 640), "letterbox 출력은 정사각")
    check(pad[1] > 0 and pad[0] == 0, "가로가 긴 프레임은 위아래에 패딩")

    # 원본 픽셀 (192, 108) ~ (960, 540) 을 letterbox 좌표로 옮긴 뒤 되돌린다
    lb = np.array([192.0, 108.0, 960.0, 540.0]) * ratio + np.array(
        [pad[0], pad[1], pad[0], pad[1]])
    x1, y1, x2, y2 = inference._scale_back(lb, ratio, pad, 1920, 1080)
    check(abs(x1 - 0.1) < 1e-3 and abs(y1 - 0.1) < 1e-3, "되돌린 좌상단이 0.1, 0.1")
    check(abs(x2 - 0.5) < 1e-3 and abs(y2 - 0.5) < 1e-3, "되돌린 우하단이 0.5, 0.5")

    out = inference._scale_back([-500, -500, 99999, 99999], ratio, pad, 1920, 1080)
    check(out == (0.0, 0.0, 1.0, 1.0), "프레임을 벗어난 박스는 0~1 로 잘린다")


# ────────────────────────────────────────────────── 3. 전이 검출

def test_debounce() -> None:
    print("전이 검출")
    d = Debouncer(hold_sec=3.0, min_conf=0.4)
    box = {"x1": 0.1, "y1": 0.1, "x2": 0.3, "y2": 0.4, "label": "ITEM-A", "score": 0.9}

    t = d.observe(1, 100.0, {"ITEM-A": [box]})
    check(len(t) == 1 and t[0].state == "active", "처음 보면 즉시 발생")

    t = d.observe(1, 100.4, {"ITEM-A": [box]})
    check(t == [], "계속 보이는 동안은 아무 것도 발행하지 않는다")

    t = d.observe(1, 101.0, {})
    check(t == [], "잠깐 놓친 것으로 해제하지 않는다")

    t = d.observe(1, 103.5, {})
    check(len(t) == 1 and t[0].state == "inactive", "hold_sec 뒤에 해제")

    t = d.observe(1, 104.0, {"ITEM-A": [box]})
    check(len(t) == 1 and t[0].state == "active", "해제 뒤 다시 보면 또 발생")

    low = dict(box, score=0.2)
    d2 = Debouncer(hold_sec=3.0, min_conf=0.4)
    check(d2.observe(2, 100.0, {"ITEM-A": [low]}) == [], "min_conf 미달은 발생시키지 않는다")

    # 낮은 신뢰도가 해제를 무한히 미루지 못해야 한다
    d3 = Debouncer(hold_sec=2.0, min_conf=0.4)
    d3.observe(3, 100.0, {"ITEM-A": [box]})
    d3.observe(3, 101.0, {"ITEM-A": [low]})
    t = d3.observe(3, 102.5, {"ITEM-A": [low]})
    check(len(t) == 1 and t[0].state == "inactive",
          "낮은 신뢰도로 활성이 연장되지 않는다")

    # 항목 둘이 같은 카메라에서 동시에 활성
    d4 = Debouncer(hold_sec=2.0, min_conf=0.4)
    t = d4.observe(4, 100.0, {"ITEM-A": [box], "ITEM-B": [box]})
    check(len(t) == 2, "항목마다 따로 상태를 갖는다")
    t = d4.observe(4, 100.5, {"ITEM-A": [box]})
    check(t == [], "한 항목만 보이는 것으로 다른 항목이 바뀌지 않는다")
    t = d4.observe(4, 103.0, {"ITEM-A": [box]})
    check(len(t) == 1 and t[0].item == "ITEM-B", "안 보이는 항목만 해제된다")

    # 스트림이 끊겼을 때 활성이 남지 않아야 한다
    d5 = Debouncer(hold_sec=100.0, min_conf=0.4)
    d5.observe(5, 100.0, {"ITEM-A": [box]})
    t = d5.close_camera(5, 101.0)
    check(len(t) == 1 and t[0].state == "inactive", "스트림 종료 시 활성을 해제한다")
    check(d5.active_items() == [], "정리 후 활성 목록이 비어 있다")


# ────────────────────────────────────────────────── 4. 플랫폼 계약 일치

def test_platform_contract() -> None:
    """우리가 발행하는 페이로드를 플랫폼의 mapping 이 그대로 읽는지 본다.

    바인딩 프리셋 '우리 모듈 (탐지)' 이 쓰는 표현식을 그대로 적용한다. 프리셋이 특권이
    아니라 폼 기본값일 뿐이라는 것과 별개로, 우리 모듈이 그 모양으로 보내는지는 맞아야 한다.
    """
    print("플랫폼 계약 일치")
    server = Path(__file__).resolve().parents[2] / "server"
    if not (server / "aivision_server" / "mqtt" / "mapping.py").is_file():
        print("  건너뜀 - 플랫폼 소스를 찾을 수 없습니다")
        return
    sys.path.insert(0, str(server))
    from aivision_server.mqtt import mapping

    det = inference.Detection(0.1, 0.2, 0.3, 0.55, 0.884, 0, "no_helmet")
    payload = {
        "camera_id": 7,
        "module_id": "yolo-server",
        "item": "ITEM-001",
        "state": "active",
        "ts": "2026-09-08T02:00:00Z",
        "confidence": 0.884,
        "boxes": [dict(det.to_box(), label="ITEM-001")],
    }
    topic = "aivision/detect/7/yolo-server"
    flat = mapping.preprocess(payload, "raw")

    check(mapping.topic_matches("aivision/detect/#", topic), "탐지 토픽이 프리셋 패턴에 걸린다")
    check(mapping.evaluate("$.camera_id", topic=topic, payload=flat) == 7, "카메라 표현식")
    check(mapping.evaluate("$.item", topic=topic, payload=flat) == "ITEM-001", "항목 표현식")
    check(mapping.evaluate("$.state", topic=topic, payload=flat) == "active", "상태 표현식")
    conf = mapping.to_float(mapping.evaluate("$.confidence", topic=topic, payload=flat))
    check(abs(conf - 0.884) < 1e-9, "신뢰도가 0~1 그대로 유지된다")
    check(mapping.to_time(mapping.evaluate("$.ts", topic=topic, payload=flat)) is not None,
          "시각 표현식")

    raw_boxes = mapping.evaluate("$.boxes", topic=topic, payload=flat)
    boxes = mapping.to_boxes(raw_boxes, "xyxy_norm")
    check(len(boxes) == 1, "박스 하나가 그대로 읽힌다")
    b = boxes[0]
    check(abs(b["x1"] - 0.1) < 1e-6 and abs(b["y2"] - 0.55) < 1e-6, "박스 좌표 보존")
    check(b["label"] == "ITEM-001" and abs(b["score"] - 0.884) < 1e-3, "라벨·점수 보존")

    # 라이브 토픽은 카메라를 토픽 조각에서 읽는다 (프리셋 '우리 모듈 (라이브 박스)')
    live_topic = "aivision/live/7"
    check(mapping.topic_matches("aivision/live/+", live_topic), "라이브 토픽 패턴")
    check(str(mapping.evaluate("$topic[2]", topic=live_topic, payload={})) == "7",
          "라이브 토픽의 카메라 조각")


def main() -> int:
    for fn in (test_output_shapes, test_scale_back, test_debounce, test_platform_contract):
        fn()
    print(f"\n검증 {CHECKS}개 통과 ({time.strftime('%H:%M:%S')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

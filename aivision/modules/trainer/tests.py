"""학습·평가 플러그인 자체 검증 — 브로커도 모델도 카메라도 없이 돈다.

  1. 판별률 평가   정답 읽기·좌표 변환·클래스 이름·일치 판정 (evaluate.py)
  2. 라벨링        라벨 파일 읽고 쓰기, 모델 초안을 데이터셋 클래스로 잇기 (dataset_api.py)
  3. 판독 공유     객체감지 플러그인과 같은 판독 코드를 담았는가

실행: python tests.py   (컨테이너 안, 또는 저장소를 붙여서)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# 저장소에서 돌릴 때: 판독 코드(inference·config·settings)는 객체감지 폴더에 있고, _sdk 는
# 그 위에 있다. 컨테이너 안에서는 모두 /app 에 같이 놓인다.
for extra in (HERE.parent / "yolo", HERE.parent):
    if extra.is_dir():
        sys.path.append(str(extra))

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def test_evaluation() -> None:
    """이미지 판별률 평가(evaluate.py) — 정답 읽기와 일치 판정.

    시험 결과를 좌우하는 부분이라 경계를 하나씩 못 박는다. 모델 없이 도는 부분만 본다.
    """
    print("판별률 평가")
    import tempfile

    import evaluate as ev

    B = ev.Box
    # ── 일치 판정: 같은 클래스끼리 IoU 0.5 이상, 놓침·헛탐지가 없어야 일치
    amr, hum = B("AMR", .10, .10, .30, .30), B("Human", .50, .50, .60, .80)
    v = ev.match([amr, hum], [B("Human", .51, .50, .61, .80, .9), B("AMR", .11, .10, .31, .30, .8)])
    check(v.ok and len(v.matched) == 2, "순서가 달라도 같은 객체끼리 짝짓는다")
    v = ev.match([amr], [B("Human", .10, .10, .30, .30, .9)])
    check(not v.ok and v.missed == [0] and v.false_pos == [0], "자리가 같아도 클래스가 틀리면 불일치")
    v = ev.match([amr, hum], [B("AMR", .10, .10, .30, .30, .9)])
    check(not v.ok and v.missed == [1] and not v.false_pos, "하나 놓치면 불일치")
    v = ev.match([amr], [B("AMR", .10, .10, .30, .30, .9), B("AMR", .70, .70, .80, .80, .7)])
    check(not v.ok and v.false_pos == [1], "헛탐지가 하나라도 있으면 불일치")
    check(ev.match([], []).ok, "정답도 탐지도 없으면 일치")
    check(not ev.match([], [B("Human", .1, .1, .2, .2, .9)]).ok, "정답이 없는데 잡으면 불일치")
    v = ev.match([amr], [B("AMR", .20, .10, .40, .30, .9)])        # IoU 1/3
    check(not v.ok, "IoU 0.5 미만은 같은 객체로 보지 않는다")
    # 탐욕 매칭: 두 정답이 한 탐지를 다투면 더 많이 겹친 쪽이 가져간다
    t1, t2 = B("AMR", .10, .10, .30, .30), B("AMR", .12, .10, .32, .30)
    v = ev.match([t1, t2], [B("AMR", .12, .10, .32, .30, .9)])
    check(v.matched[0][0] == 1 and v.missed == [0], "더 많이 겹친 정답이 짝을 가져간다")
    check(abs(ev.iou(amr, amr) - 1.0) < 1e-9 and ev.iou(amr, hum) == 0.0, "IoU 경계값")

    # 박스 색이 대시보드(boxcolor.ts)와 같아야 한다 — 값은 JS 로 직접 계산해 둔 것
    check(ev._djb2("AMR") == 193450405 and ev._djb2("Human") == 223865182,
          "색 해시가 대시보드와 같다")
    check(ev.box_color("AMR") == (0xae, 0xb8, 0x3f), "AMR 은 대시보드와 같은 청록")
    check(ev.box_color("Human", {"Human": "#ff0000"}) == (0, 0, 255), "관리자에서 덮어쓴 색을 따른다")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "images").mkdir()
        (root / "labels").mkdir()
        for n in ("a", "b"):
            (root / "images" / f"{n}.jpg").write_bytes(b"x")

        # 정답이 하나도 없으면 판독 전용으로 내려간다
        _, info = ev.load_set(root)
        check(info["mode"] == "predict-only", "정답이 없으면 판독 전용")

        # 일부만 있으면 멈춘다 — 조용히 빼면 분모가 줄어 판별률이 부풀려진다
        (root / "labels" / "a.txt").write_text("0 0.2 0.2 0.2 0.2\n", encoding="utf-8")
        try:
            ev.load_set(root)
        except ev.EvalError as exc:
            check("b.jpg" in str(exc), "정답이 빠진 이미지를 짚어 멈춘다")
        else:
            check(False, "정답이 빠진 이미지를 짚어 멈춘다")

        # classes.txt 가 없으면 0=AMR, 1=Human 으로 가정하고 그렇다고 남긴다
        (root / "labels" / "b.txt").write_text("1 0.5 0.5 0.2 0.4\n\n# 주석\n", encoding="utf-8")
        samples, info = ev.load_set(root)
        check(info["class_names_assumed"] and info["mode"] == "evaluate", "클래스 이름 가정")
        b = next(s for s in samples if s.image.name == "b.jpg").truth[0]
        check(b.cls == "Human" and abs(b.x1 - .4) < 1e-9 and abs(b.y2 - .7) < 1e-9,
              "YOLO 중심·크기를 모서리 좌표로 옮긴다")

        # 라벨링 툴의 이름(person·물류로봇)을 평가 이름으로 흡수한다. 순서도 파일을 따른다
        (root / "classes.txt").write_text("person\n물류로봇\n", encoding="utf-8")
        samples, info = ev.load_set(root)
        check(not info["class_names_assumed"] and info["class_names"] == ["Human", "AMR"],
              "classes.txt 의 이름과 순서를 따른다")
        a = next(s for s in samples if s.image.name == "a.jpg").truth[0]
        check(a.cls == "Human", "번호 0 이 classes.txt 첫 줄(person→Human)로 읽힌다")

        (root / "classes.txt").write_text("forklift\n", encoding="utf-8")
        try:
            ev.load_set(root)
        except ev.EvalError:
            check(True, "알 수 없는 클래스 이름은 멈춘다")
        else:
            check(False, "알 수 없는 클래스 이름은 멈춘다")
        (root / "classes.txt").unlink()

        (root / "labels" / "a.txt").write_text("0 0.2 0.2\n", encoding="utf-8")
        try:
            ev.load_set(root)
        except ev.EvalError as exc:
            check("a.txt:1" in str(exc), "깨진 줄은 파일·줄 번호를 짚어 멈춘다")
        else:
            check(False, "깨진 줄은 파일·줄 번호를 짚어 멈춘다")

        # results/ 아래의 결과 이미지를 데이터로 읽으면 안 된다
        (root / "results" / "r1" / "annotated").mkdir(parents=True)
        (root / "results" / "r1" / "annotated" / "a.jpg").write_bytes(b"x")
        _, imgs = ev.find_images(root)
        check(len(imgs) == 2, "결과 폴더의 이미지는 데이터가 아니다")


def test_labeling() -> None:
    """데이터셋·라벨링(dataset_api.py) — 라벨 파일 읽고 쓰기와 초안 클래스 잇기."""
    print("라벨링")
    import tempfile

    import dataset_api as da

    # ── 쓰고 다시 읽으면 같은 박스. 뒤집힌 박스(오른쪽에서 왼쪽으로 끈 것)는 바로 세운다
    boxes = [{"cls": 0, "x1": .1, "y1": .2, "x2": .3, "y2": .5},
             {"cls": 1, "x1": .9, "y1": .8, "x2": .6, "y2": .4}]
    text = da.yolo_text(boxes)
    check(text.splitlines()[0] == "0 0.200000 0.350000 0.200000 0.300000", "YOLO 줄: cls cx cy w h")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "a.txt"
        p.write_text(text + "# 메모\n\nbad line\n", encoding="utf-8")
        back = da.read_boxes(p)
        check(len(back) == 2 and back[1]["x1"] == 0.6 and back[1]["y2"] == 0.8,
              "뒤집힌 박스를 세워 저장하고, 주석·깨진 줄은 건너뛰어 읽는다")
        check(all(abs(back[0][k] - boxes[0][k]) < 1e-6 for k in ("x1", "y1", "x2", "y2")),
              "쓰고 읽은 좌표가 같다")
    check(da.yolo_text([{"cls": 0, "x1": .5, "y1": .5, "x2": .5, "y2": .6}]) == "",
          "폭이 0 인 박스(클릭 한 번)는 저장하지 않는다")
    check(da.yolo_text([]) == "", "박스가 없으면 빈 파일 — '아무것도 없다' 는 정답")
    check(da.yolo_text([{"cls": 0, "x1": -.2, "y1": .1, "x2": 1.4, "y2": .3}]).split()[1:4:2]
          == ["0.500000", "1.000000"], "이미지 밖으로 나간 박스는 안쪽으로 자른다")

    # ── 초안: 모델 클래스(person, AMR)를 데이터셋 클래스 번호로
    cmap, index = da.class_map_for(["AMR", "Human"])
    check(cmap["person"] == "Human" and index["Human"] == 1 and index["AMR"] == 0,
          "COCO 의 person 은 데이터셋의 Human 으로 간다")
    cmap, index = da.class_map_for(["사람", "로봇", "forklift"])
    check(index["Human"] == 0 and index["AMR"] == 1 and index["forklift"] == 2,
          "데이터셋 클래스를 한글로 지어도 초안이 붙는다")


def test_shared_inference() -> None:
    """평가·초안이 실시간 화면과 같은 판독을 하는지 — 같은 코드를 쓰는지 본다."""
    print("판독 공유")
    import evaluate
    import inference

    det = inference.Detection(x1=0.1, y1=0.1, x2=0.2, y2=0.3, score=0.9, cls=0, label="AMR")
    check(abs(inference.short_side_px(det, 1000, 1000) - (100 * 200) ** 0.5) < 1e-6,
          "작은 박스 거르기는 객체감지 inference.short_side_px 를 쓴다")
    check("short_side_px" in Path(evaluate.__file__).read_text(encoding="utf-8"),
          "evaluate.Detector 가 같은 거르기를 부른다")


def main() -> int:
    for fn in (test_evaluation, test_labeling, test_shared_inference):
        fn()
    print(f"\n검증 {CHECKS}개 통과 ({time.strftime('%H:%M:%S')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

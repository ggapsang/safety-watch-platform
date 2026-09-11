"""weights.pt -> weights.onnx (일회성 변환)

**왜 필요한가.** 객체감지 모듈은 ONNX 만 받는다. torch 를 담으면 이미지가 3GB 를 넘어
걷어냈기 때문이다. 그런데 .pt 를 ONNX 로 바꾸려면 바로 그 torch 가 필요하다. 그래서
모듈 이미지가 아니라 **일회용 컨테이너**에서 돌린다(export_onnx.sh 참조).

**어느 계열인지 자동으로 가른다.** 이 저장소에는 yolov7 학습 코드가 있어서(refs/) 처음엔
그쪽 export.py 를 썼는데, 실제 받은 가중치는 ultralytics(YOLOv8/v11 계열)였다. 둘은
체크포인트 구조도 내보내기 방법도 다르다. 사람이 매번 알아내게 하지 않고 열어 보고 고른다.

**출력 규약.** 모듈의 inference.py 가 세 가지 출력 모양을 런타임에 판별한다.
ultralytics 내보내기는 (1, 4+nc, N) 로 나오고 클래스 이름을 메타데이터에 심어 주므로,
모듈 화면이 탐지 대상 표를 자동으로 채운다. 잘 안 되면 LAYOUT=v8 로 못 박으면 된다.

주의: ultralytics 는 AGPL-3.0 이다. 이 변환에만 쓰고 제품 이미지에는 들어가지 않는다 —
결과물인 .onnx 파일만 모듈이 읽는다.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def kind(path: Path) -> str:
    """체크포인트를 열지 않고 어느 계열인지 본다.

    .pt 는 zip 이고 그 안의 피클에 클래스 경로가 문자열로 들어 있다. 풀어서 읽으면
    torch 없이도, 그 프레임워크를 설치하지 않고도 가늠할 수 있다. 잘못 짚어 몇 분짜리
    설치를 한 뒤에야 실패하는 것을 막는 자리다.
    """
    try:
        with zipfile.ZipFile(path) as z:
            blob = b"".join(z.read(n) for n in z.namelist()
                            if n.endswith((".pkl", "data.pkl")))
    except (zipfile.BadZipFile, KeyError):
        return "unknown"
    if b"ultralytics" in blob:
        return "ultralytics"
    if b"models.yolo" in blob or b"models.common" in blob:
        return "yolov7"
    return "unknown"


def export_ultralytics(src: Path, imgsz: int) -> Path:
    from ultralytics import YOLO

    model = YOLO(str(src))
    names = getattr(model, "names", None) or {}
    print(f"클래스 {len(names)}개: {', '.join(str(v) for v in names.values())}")
    # opset 12 는 onnxruntime 이 오래전부터 받는 값이다. 높일 이유가 없다.
    # simplify 는 그래프를 정리해 준다 — 실패해도 내보내기 자체는 된다.
    out = model.export(format="onnx", imgsz=imgsz, simplify=True, opset=12, dynamic=False)
    return Path(out)


def main(argv: list[str]) -> int:
    here = Path(__file__).resolve().parent
    src = here / (argv[0] if argv else "weights.pt")
    imgsz = int(argv[1]) if len(argv) > 1 else 640

    if not src.is_file():
        print(f"가중치를 찾을 수 없습니다: {src}", file=sys.stderr)
        return 1

    family = kind(src)
    print(f"{src.name} — {family} 계열로 판단했습니다 (img-size {imgsz})")
    if family == "yolov7":
        print("yolov7 계열입니다. refs/yolov7-training/vendor/yolov7/export.py 로 "
              "내보내야 합니다 — export_onnx.sh 의 주석을 보십시오.", file=sys.stderr)
        return 2
    if family == "unknown":
        print("계열을 알 수 없습니다. ultralytics 로 시도합니다.", file=sys.stderr)

    out = export_ultralytics(src, imgsz)
    if not out.is_file():
        print("내보내기는 끝났는데 결과 파일이 없습니다.", file=sys.stderr)
        return 1
    print(f"완료: {out.name} ({out.stat().st_size / 1024 / 1024:.1f}MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

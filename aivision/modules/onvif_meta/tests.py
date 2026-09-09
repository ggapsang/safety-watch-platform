"""파서 검증 — 카메라도 브로커도 없이 돈다.

표본은 실제 카메라(PNO-A9082R)에서 뜬 것을 그대로 옮겼다. 좌표 변환이 조용히 어긋나면
박스가 엉뚱한 곳에 그려지는데, 화면을 보기 전에는 알아채기 어렵다. 그래서 여기서 잡는다.

실행: python tests.py
"""

from __future__ import annotations

import sys

import metadata

CHECKS = 0

# 실제 프레임. Scale x=0.001042(=2/1920), y=-0.001852(=2/1080) 이므로 기준은 1920x1080 이다.
FRAME = b"""<?xml version="1.0" encoding="UTF-8"?><tt:MetadataStream
 xmlns:tt="http://www.onvif.org/ver10/schema"><tt:VideoAnalytics>
<tt:Frame UtcTime="2026-09-09T00:33:23.120Z">
<tt:Transformation><tt:Translate x="-1.0" y="1.0"/><tt:Scale x="0.001042" y="-0.001852"/>
</tt:Transformation>
<tt:Object ObjectId="7"><tt:Appearance>
<tt:Shape><tt:BoundingBox left="480.0" top="270.0" right="960.0" bottom="540.0"/>
<tt:CenterOfGravity x="720.0" y="405.0"/></tt:Shape>
<tt:Class><tt:ClassCandidate><tt:Type>Human</tt:Type><tt:Likelihood>0.41</tt:Likelihood>
</tt:ClassCandidate><tt:Type Likelihood="0.41">Human</tt:Type></tt:Class>
</tt:Appearance></tt:Object>
</tt:Frame></tt:VideoAnalytics></tt:MetadataStream>"""

EMPTY_FRAME = b"""<?xml version="1.0" encoding="UTF-8"?><tt:MetadataStream
 xmlns:tt="http://www.onvif.org/ver10/schema"><tt:VideoAnalytics>
<tt:Frame UtcTime="2026-09-09T00:33:23.120Z"><tt:Transformation>
<tt:Translate x="-1.0" y="1.0"/><tt:Scale x="0.001042" y="-0.001852"/></tt:Transformation>
</tt:Frame></tt:VideoAnalytics></tt:MetadataStream>"""

EVENT_DOC = b"""<?xml version="1.0" encoding="UTF-8"?><tt:MetadataStream
 xmlns:tt="http://www.onvif.org/ver10/schema"
 xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2"
 xmlns:tns1="http://www.onvif.org/ver10/topics"><tt:Event><wsnt:NotificationMessage>
<wsnt:Topic Dialect="...">tns1:AudioSource/tnssamsung:AudioDetection</wsnt:Topic>
<wsnt:Message><tt:Message UtcTime="2026-09-09T00:17:52.003Z"><tt:Data>
<tt:SimpleItem Name="State" Value="false"/></tt:Data></tt:Message></wsnt:Message>
</wsnt:NotificationMessage></tt:Event></tt:MetadataStream>"""

NO_TRANSFORM = FRAME.replace(
    b'<tt:Transformation><tt:Translate x="-1.0" y="1.0"/>'
    b'<tt:Scale x="0.001042" y="-0.001852"/>\n</tt:Transformation>', b"")


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def test_boxes() -> None:
    print("박스 파싱")
    boxes = metadata.parse_boxes(FRAME)
    check(boxes is not None and len(boxes) == 1, "객체 하나를 읽는다")
    b = boxes[0]
    # 480/1920 = 0.25, 270/1080 = 0.25, 960/1920 = 0.5, 540/1080 = 0.5
    check(abs(b["x1"] - 0.25) < 1e-3 and abs(b["y1"] - 0.25) < 1e-3, "좌상단 정규화")
    check(abs(b["x2"] - 0.5) < 1e-3 and abs(b["y2"] - 0.5) < 1e-3, "우하단 정규화")
    check(b["label"] == "Human" and abs(b["score"] - 0.41) < 1e-6, "클래스와 신뢰도")

    named = metadata.parse_boxes(FRAME, {"Human": "사람"})
    check(named[0]["label"] == "사람", "이름 바꾸기(class_map)")

    filtered = metadata.parse_boxes(FRAME, {"Vehicle": "차량"}, keep_unmapped=False)
    check(filtered == [], "표에 없는 클래스는 걸러낼 수 있다")

    check(metadata.parse_boxes(FRAME, skip={"human"}) == [], "제외 목록의 클래스는 안 그린다")
    check(len(metadata.parse_boxes(FRAME, skip={"face", "head"})) == 1,
          "제외 목록에 없는 클래스는 그대로 그린다")
    check(metadata.parse_boxes(FRAME, {"Human": "사람"}, skip={"human"}) == [],
          "제외는 이름 바꾸기 전의 카메라 원래 이름으로 판단한다")


def test_frame_kinds() -> None:
    print("문서 종류 구분")
    check(metadata.parse_boxes(EMPTY_FRAME) == [],
          "객체 없는 프레임은 빈 목록 (화면의 박스를 지워야 한다)")
    check(metadata.parse_boxes(EVENT_DOC) is None,
          "이벤트 알림은 None (화면을 건드리지 않는다)")
    check(metadata.event_topic(EVENT_DOC).endswith("AudioDetection"), "이벤트 토픽 추출")
    check(metadata.parse_boxes(b"<?xml version='1.0'?><nope") is None, "깨진 문서는 None")
    check(metadata.parse_boxes(NO_TRANSFORM) == [],
          "좌표 기준이 없으면 그리지 않는다 (틀린 자리에 그리는 것보다 낫다)")


def test_split() -> None:
    print("스트림 조각 잇기")
    stream = FRAME + EMPTY_FRAME + FRAME[:40]
    docs, tail = metadata.split_documents(stream)
    check(len(docs) == 2, "완성된 문서 둘을 잘라낸다")
    check(tail == FRAME[:40], "잘린 조각은 다음 읽기로 넘긴다")

    docs, tail = metadata.split_documents(FRAME[:30])
    check(docs == [] and tail == FRAME[:30], "문서가 덜 왔으면 그대로 들고 있는다")

    # 실제 스트림처럼 잘게 쪼개 넣어도 결과가 같아야 한다.
    # 마지막 문서는 다음 문서가 시작될 때 확정되므로(경계를 그때 알 수 있다) 뒤에
    # 하나를 더 붙여 준다. 실제 스트림은 계속 이어지므로 이 지연은 문서 하나 분이다.
    buf, boxes, empties = b"", 0, 0
    blob = FRAME + EVENT_DOC + EMPTY_FRAME + FRAME + EVENT_DOC
    for i in range(0, len(blob), 64):
        buf += blob[i:i + 64]
        docs, buf = metadata.split_documents(buf)
        for d in docs:
            parsed = metadata.parse_boxes(d)
            if parsed:
                boxes += 1
            elif parsed == []:
                empties += 1
    check(boxes == 2 and empties == 1,
          f"조각 단위로 넣어도 박스 2건·빈 프레임 1건 (실제 {boxes}, {empties})")


def main() -> int:
    for fn in (test_boxes, test_frame_kinds, test_split):
        fn()
    print(f"\n검증 {CHECKS}개 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""ONVIF 메타데이터 파서 — 카메라가 이미 만들어 놓은 박스를 읽는다.

한화비전 카메라는 RTSP 스트림에 데이터 트랙을 하나 더 실어 보낸다. 그 안에 WiseAI 등이
만든 `tt:MetadataStream` 이 흐르고, 객체마다 BoundingBox 와 클래스·신뢰도가 들어 있다.

    <tt:Frame UtcTime="...">
      <tt:Transformation><tt:Translate x="-1.0" y="1.0"/>
                         <tt:Scale x="0.001042" y="-0.001852"/></tt:Transformation>
      <tt:Object ObjectId="2083" Parent="2080"><tt:Appearance>
        <tt:Shape><tt:BoundingBox left="2979" top="849" right="3165" bottom="1047"/></tt:Shape>
        <tt:Class><tt:Type Likelihood="0.57">Face</tt:Type></tt:Class>
      </tt:Appearance></tt:Object>
    </tt:Frame>

좌표는 픽셀이고, 그 픽셀이 어느 크기 기준인지는 **프레임이 직접 알려 준다**(Transformation).
ONVIF 규약대로 `x*Scale + Translate` 를 적용하면 [-1, 1] 정규 좌표가 되고, y 축은 위가 +다.
우리 규약(0~1, y 는 아래로)으로 옮기려면 한 번 더 접는다. 해상도를 설정으로 받지 않는
이유가 이것이다 — 스트림 프로파일이 바뀌어도 프레임이 스스로 기준을 들고 온다.

MJPEG/H.264 트랙은 건드리지 않는다. 이 모듈은 영상을 디코딩하지 않는다.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

log = logging.getLogger(__name__)

TT = "{http://www.onvif.org/ver10/schema}"
DOC_START = b"<?xml"
MAX_BUFFER = 4 * 1024 * 1024        # 문서 경계를 못 찾는 이상 상황에서 메모리를 지킨다


@dataclass
class Transform:
    """프레임이 알려 준 좌표 기준. 없으면 좌표를 그대로 믿을 수 없다."""

    scale_x: float
    scale_y: float
    translate_x: float
    translate_y: float

    def to_unit(self, x: float, y: float) -> tuple[float, float]:
        """픽셀 -> ONVIF 정규([-1,1], y 위로) -> 우리 규약(0~1, y 아래로)."""
        nx = x * self.scale_x + self.translate_x
        ny = y * self.scale_y + self.translate_y
        return (nx + 1.0) / 2.0, (1.0 - ny) / 2.0


def split_documents(buffer: bytes) -> tuple[list[bytes], bytes]:
    """이어 붙은 XML 문서들을 잘라 낸다. (완성된 문서들, 남은 조각) 을 돌려준다.

    RTP 로 오는 메타데이터는 문서 하나씩 깔끔하게 도착하지 않는다. 앞머리(`<?xml`)를
    경계로 삼고, 마지막 조각은 다음 읽기와 이어 붙인다.

    그래서 **문서 하나만큼 늦게 확정된다** — 마지막 문서는 다음 문서가 시작되어야
    끝났음을 알 수 있기 때문이다. 메타데이터는 초당 열 건 안팎으로 계속 오므로 실사용에서
    이 지연은 100ms 안팎이고, 끝난 뒤 남는 한 건은 스트림이 끊겼다는 뜻이라 어차피 버린다.
    """
    parts = buffer.split(DOC_START)
    if len(parts) <= 1:
        if len(buffer) > MAX_BUFFER:
            log.warning("메타데이터 버퍼가 %d바이트인데 문서 경계를 못 찾았습니다 — 버립니다",
                        len(buffer))
            return [], b""
        return [], buffer
    docs = [DOC_START + p for p in parts[1:-1]]
    tail = DOC_START + parts[-1]
    return docs, tail


def _float(value: str | None) -> float | None:
    try:
        return float(value)                                  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _transform_of(frame: ET.Element) -> Transform | None:
    node = frame.find(f"{TT}Transformation")
    if node is None:
        return None
    scale = node.find(f"{TT}Scale")
    translate = node.find(f"{TT}Translate")
    if scale is None or translate is None:
        return None
    sx, sy = _float(scale.get("x")), _float(scale.get("y"))
    tx, ty = _float(translate.get("x")), _float(translate.get("y"))
    if None in (sx, sy, tx, ty) or not sx or not sy:
        return None
    return Transform(sx, sy, tx, ty)                         # type: ignore[arg-type]


def _class_of(obj: ET.Element) -> tuple[str, float]:
    """가장 유력한 클래스와 신뢰도. 없으면 ('Unknown', 0.0)."""
    best_name, best_score = "", 0.0
    for node in obj.iter(f"{TT}Type"):
        name = (node.text or "").strip()
        score = _float(node.get("Likelihood")) or 0.0
        if name and score >= best_score:
            best_name, best_score = name, score
    if best_name:
        return best_name, best_score
    # Likelihood 없이 Type 만 오는 경우도 있다. 그때는 '봤다'는 사실만 쓴다.
    node = obj.find(f".//{TT}Type")
    return ((node.text or "").strip() or "Unknown", 0.0) if node is not None else ("Unknown", 0.0)


def parse_boxes(doc: bytes, class_map: dict[str, str] | None = None,
                keep_unmapped: bool = True) -> list[dict] | None:
    """문서 하나에서 박스를 뽑는다.

    객체가 실린 프레임이 아니면 None 을 돌려준다. **빈 리스트와 None 은 다르다** —
    빈 리스트는 '이 프레임에는 아무 것도 없다'(화면의 박스를 지워야 한다)이고,
    None 은 '이 문서는 박스 이야기가 아니다'(이벤트 알림 등, 화면을 건드리지 말라)이다.
    """
    try:
        root = ET.fromstring(doc.decode("utf-8", "replace"))
    except ET.ParseError:
        return None

    out: list[dict] = []
    saw_frame = False
    for frame in root.iter(f"{TT}Frame"):
        saw_frame = True
        transform = _transform_of(frame)
        for obj in frame.iter(f"{TT}Object"):
            box = obj.find(f".//{TT}BoundingBox")
            if box is None:
                continue
            left, top = _float(box.get("left")), _float(box.get("top"))
            right, bottom = _float(box.get("right")), _float(box.get("bottom"))
            if None in (left, top, right, bottom):
                continue
            if transform is None:
                # 기준을 모르면 좌표를 지어내지 않는다. 그리는 것보다 안 그리는 게 낫다.
                continue
            x1, y1 = transform.to_unit(left, top)              # type: ignore[arg-type]
            x2, y2 = transform.to_unit(right, bottom)          # type: ignore[arg-type]
            if x2 < x1:
                x1, x2 = x2, x1
            if y2 < y1:
                y1, y2 = y2, y1

            name, score = _class_of(obj)
            label = (class_map or {}).get(name, "")
            if not label:
                if not keep_unmapped:
                    continue
                label = name
            out.append({
                "x1": round(max(0.0, min(1.0, x1)), 4),
                "y1": round(max(0.0, min(1.0, y1)), 4),
                "x2": round(max(0.0, min(1.0, x2)), 4),
                "y2": round(max(0.0, min(1.0, y2)), 4),
                "label": label,
                "score": round(score, 3),
            })
    return out if saw_frame else None


_EVENT_TOPIC = re.compile(rb"<wsnt:Topic[^>]*>([^<]+)</wsnt:Topic>")


def event_topic(doc: bytes) -> str:
    """이 문서가 ONVIF 이벤트 알림이면 그 토픽. 진단 로그용."""
    m = _EVENT_TOPIC.search(doc)
    return m.group(1).decode("utf-8", "replace") if m else ""

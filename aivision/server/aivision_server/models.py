"""DB 스키마.

설계 원칙
  · 카메라·탐지항목·인바운드 바인딩은 **데이터**다. 코드에 하드코딩하지 않는다.
    현장에서 카메라를 추가하거나 새 소스를 받아들여도 재배포가 필요 없어야 한다.
  · 이벤트는 **어디서 판정했든 같은 테이블**에 쌓인다(source 컬럼으로만 구분).
    카메라 엣지든 서버 YOLO 든 협력사 모듈이든 뒤가 같아진다.
  · 통계 화면은 events 만 보고 그린다 — 소스가 늘어도 통계 코드는 그대로다.
  · 아직 정하지 않은 분류(심각도·처리 상태 등)는 컬럼으로 만들지 않는다. 미리 만들어 두면
    화면에 근거 없는 값이 뜨고, 진짜 기준이 정해졌을 때 그것부터 지워야 한다.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


# ────────────────────────────────────────────────────────────── 열거형

class SourceKind(str, enum.Enum):
    """이벤트가 어느 통로로 들어왔는지. 무엇이 판정했는지는 module_id 가 말한다."""

    MQTT = "mqtt"        # 브로커를 통해 들어옴 (카메라 엣지·사이드카·원격 모듈 공통)
    HTTP = "http"        # HTTP 인바운드로 밀어넣음
    INTERNAL = "internal"  # 서버 프로세스 안의 모듈이 직접 호출
    MANUAL = "manual"    # 운영자가 수동 등록


# ────────────────────────────────────────────────────────────── 마스터

class Solution(Base):
    """AI 솔루션(탐지 종목). 화면의 '솔루션' 필터·통계 축이 이 테이블이다."""

    __tablename__ = "solutions"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)          # SOL-004
    name: Mapped[str] = mapped_column(String(64))                            # 조기 화재 감지
    short_name: Mapped[str] = mapped_column(String(32))                      # 화재 감지
    description: Mapped[str] = mapped_column(String(200), default="")
    event_type: Mapped[str] = mapped_column(String(64))                      # 이벤트 표시명
    color: Mapped[str] = mapped_column(String(16), default="#cc785c")        # 차트 색
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    cameras: Mapped[list["CameraSolution"]] = relationship(back_populates="solution")


class Camera(Base):
    """네트워크 카메라 1대. 어드민 화면에서 IP 로 등록한다."""

    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))                    # 현장 카메라 #1
    location: Mapped[str] = mapped_column(String(120))               # 1소성로 전실
    note: Mapped[str] = mapped_column(String(200), default="")

    # ── 접속 정보 ────────────────────────────────────────────────────
    ip: Mapped[str] = mapped_column(String(45), index=True)          # IPv4/IPv6
    rtsp_port: Mapped[int] = mapped_column(Integer, default=554)
    rtsp_path: Mapped[str] = mapped_column(String(200), default="/profile2/media.smp")
    username: Mapped[str] = mapped_column(String(64), default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")      # Fernet 암호문
    # 카메라가 MQTT 로 발행할 때 토픽 접두로 쓰는 MAC. 대문자 콜론 표기(E4:30:22:...).
    mac: Mapped[str] = mapped_column(String(23), default="", index=True)
    vendor: Mapped[str] = mapped_column(String(32), default="HANWHA")
    model: Mapped[str] = mapped_column(String(64), default="")

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # ── 녹화 정책 ────────────────────────────────────────────────────
    # 바이트를 쓰는 일은 미디어 서버가 하지만, 무엇을 언제 녹화할지는 코어가 정한다.
    record_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    record_retention_days: Mapped[int] = mapped_column(Integer, default=3)

    # 온라인 여부는 '영상 수신' 또는 'MQTT heartbeat' 중 하나라도 살아 있으면 True.
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(String(300), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    # 주의: 두 관계 모두 lazy="selectin" 이어야 한다. 비동기 세션에서는 지연 로딩이
    #   MissingGreenlet 로 터지는데, 삭제 시 cascade 가 컬렉션을 읽으려 하기 때문이다.
    solutions: Mapped[list["CameraSolution"]] = relationship(
        back_populates="camera", cascade="all, delete-orphan", lazy="selectin")
    bindings: Mapped[list["InboundBinding"]] = relationship(
        back_populates="camera", cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (UniqueConstraint("ip", "rtsp_path", name="uq_camera_endpoint"),)


class CameraSolution(Base):
    """카메라 ↔ 솔루션 매핑. 한 대가 여러 솔루션을 담당할 수 있다."""

    __tablename__ = "camera_solutions"

    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"),
                                           primary_key=True)
    solution_code: Mapped[str] = mapped_column(ForeignKey("solutions.code", ondelete="CASCADE"),
                                               primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    camera: Mapped[Camera] = relationship(back_populates="solutions")
    solution: Mapped[Solution] = relationship(back_populates="cameras", lazy="joined")


class InboundBinding(Base):
    """인바운드 바인딩 — 들어온 메시지를 내부 신호로 옮기는 규칙.

    플랫폼의 심장이다. 밖에서 오는 메시지 모양은 우리가 통제할 수 없으므로
    (한화비전은 ONVIF 양식을 MQTT 본문에 싣고, 협력사는 각자 마음대로 보낸다)
    '무엇이 걸리는가(match)' 와 '어떻게 뽑는가(extract)' 를 데이터로 들고 있는다.

    어떤 토픽도 특권을 갖지 않는다. 어드민이 토픽만 알면 무엇이든 등록해 이벤트로 만들 수
    있어야 한다. 우리가 만든 모듈이 쓰는 토픽 모양(aivision/...)도 예외가 아니다 —
    화면의 '프리셋' 은 그 모양에 맞는 바인딩을 미리 채워 주는 입력 도우미일 뿐이고,
    만들어진 결과는 다른 바인딩과 완전히 같은 데이터다. 지우면 없어진다.

    표현식 문법은 mqtt/mapping.py 참조.  $.a.b / $topic[2] / $mac / 리터럴
    """

    __tablename__ = "inbound_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80))              # "한화비전 화재"
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    transport: Mapped[str] = mapped_column(String(16), default="mqtt")   # mqtt | http
    payload_profile: Mapped[str] = mapped_column(String(16), default="raw")  # raw | onvif
    # 여러 바인딩이 동시에 걸릴 때의 순서. 작을수록 먼저.
    priority: Mapped[int] = mapped_column(Integer, default=100)
    # 켜면 이벤트로 적재하지 않고 라이브 오버레이로만 흘린다.
    live_only: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── 무엇이 걸리는가 ──────────────────────────────────────────────
    topic_pattern: Mapped[str] = mapped_column(String(300), default="")   # MQTT 와일드카드
    payload_filter: Mapped[dict | None] = mapped_column(JSON)   # {"$.Data.Type": "fire"}

    # ── 어떤 카메라인가 ──────────────────────────────────────────────
    camera_from: Mapped[str] = mapped_column(String(16), default="topic_mac")
    camera_expr: Mapped[str] = mapped_column(String(200), default="")
    camera_id: Mapped[int | None] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))

    # ── 어떤 탐지 항목인가 ───────────────────────────────────────────
    item_from: Mapped[str] = mapped_column(String(16), default="fixed")
    item_expr: Mapped[str] = mapped_column(String(200), default="")
    solution_code: Mapped[str | None] = mapped_column(
        ForeignKey("solutions.code", ondelete="CASCADE"))

    # ── 상태 ────────────────────────────────────────────────────────
    # 비우면 '메시지 수신 자체가 발생'. 채우면 그 값으로 active/inactive 를 가른다.
    state_expr: Mapped[str] = mapped_column(String(200), default="")
    state_active: Mapped[str] = mapped_column(String(64), default="active")
    state_inactive: Mapped[str] = mapped_column(String(64), default="inactive")

    # ── 부가 정보 ───────────────────────────────────────────────────
    confidence_expr: Mapped[str] = mapped_column(String(200), default="")
    ts_expr: Mapped[str] = mapped_column(String(200), default="")
    boxes_expr: Mapped[str] = mapped_column(String(200), default="")
    boxes_format: Mapped[str] = mapped_column(String(16), default="xyxy_norm")

    # 진단용 — 마지막으로 이 바인딩이 걸린 시각과 누적 횟수
    last_matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    match_count: Mapped[int] = mapped_column(Integer, default=0)

    camera: Mapped[Camera | None] = relationship(back_populates="bindings")


# ────────────────────────────────────────────────────────────── 이벤트

class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)   # EVT-00001
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    solution_code: Mapped[str] = mapped_column(ForeignKey("solutions.code", ondelete="CASCADE"),
                                               index=True)
    event_type: Mapped[str] = mapped_column(String(64))

    # source 는 '어느 통로로 왔나', module_id 는 '무엇이 판정했나'. 둘은 다른 축이다.
    source: Mapped[str] = mapped_column(String(16), default=SourceKind.MQTT.value)
    module_id: Mapped[str] = mapped_column(String(64), default="")
    binding_id: Mapped[int | None] = mapped_column(Integer)   # 어느 바인딩이 만들었나(진단용)
    confidence: Mapped[float | None] = mapped_column(Float)
    snapshot_path: Mapped[str] = mapped_column(String(300), default="")

    # 원문 보존 — 오탐 분석 시 '카메라가 실제로 뭐라고 보냈나'를 되짚을 수 있어야 한다.
    raw_topic: Mapped[str] = mapped_column(String(300), default="")
    raw_payload: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    camera: Mapped[Camera] = relationship(lazy="joined")
    solution: Mapped[Solution] = relationship(lazy="joined")
    boxes: Mapped[list["EventBox"]] = relationship(cascade="all, delete-orphan",
                                                   lazy="selectin")

    __table_args__ = (
        Index("ix_events_cam_ts", "camera_id", "ts"),
        Index("ix_events_sol_ts", "solution_code", "ts"),
    )


class EventBox(Base):
    """이벤트에 딸린 바운딩 박스.

    좌표는 **0~1 정규화**로만 저장한다. 카메라 해상도가 섞이고, 엣지 추론은 640 입력
    좌표계로 뱉고, 화면은 CSS 픽셀 위에 그리기 때문에 픽셀로 저장하면 변환 지점이 흩어진다.

    주의: 현재 한화비전 엣지 앱의 ONVIF 페이로드에는 박스 좌표가 없다. 서버 YOLO 를 붙이거나
      카메라 앱의 페이로드 계약을 확장하기 전까지 이 테이블은 비어 있다.
    """

    __tablename__ = "event_boxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    x1: Mapped[float] = mapped_column(Float)
    y1: Mapped[float] = mapped_column(Float)
    x2: Mapped[float] = mapped_column(Float)
    y2: Mapped[float] = mapped_column(Float)
    label: Mapped[str] = mapped_column(String(64), default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)


# ────────────────────────────────────────────────────────────── 부가

class AnalyticsModule(Base):
    """분석 모듈 — 영상을 보고 판정하는 주체.

    코어는 모듈이 **어디서 도는지 모른다.** 서버 프로세스 안이든, 옆 컨테이너든, 다른 PC 든,
    카메라 안이든 결과만 같은 모양으로 오면 된다(매니페스토 2번).

    모듈이 하는 일
      1. 등록한다 (여기)
      2. 자기 할당(어느 카메라를 볼지)을 물어본다 -> 스트림 주소를 받아 간다
      3. 판정 결과를 발행한다 -> 인바운드 바인딩을 지나 이벤트가 된다
      4. 살아 있다고 알린다 (heartbeat)
    """

    __tablename__ = "analytics_modules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)     # "yolo-ppe"
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16), default="external")
    # internal(서버 안) | sidecar(같은 PC 다른 컨테이너) | remote(다른 PC) | edge(카메라 안)
    description: Mapped[str] = mapped_column(String(200), default="")
    # 이 모듈이 낼 수 있는 탐지 항목 코드들. 화면에서 '무엇을 볼 수 있는 모듈인가'를 보여 준다.
    capabilities: Mapped[list | None] = mapped_column(JSON)
    endpoint: Mapped[str] = mapped_column(String(300), default="")    # 있으면 상태 조회용
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[dict | None] = mapped_column(JSON)            # heartbeat 본문 그대로
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    assignments: Mapped[list["ModuleAssignment"]] = relationship(
        back_populates="module", cascade="all, delete-orphan", lazy="selectin")


class ModuleAssignment(Base):
    """모듈 <-> 카메라 할당. '이 모듈이 이 카메라를 본다'.

    옵션(ROI·스케줄·임계값)은 모듈마다 다르므로 JSON 으로 둔다. 코어는 그 내용을 해석하지
    않고 모듈에 그대로 넘긴다 — 코어가 탐지 파라미터를 이해하기 시작하면 매니페스토 2번이 깨진다.
    """

    __tablename__ = "module_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    module_id: Mapped[str] = mapped_column(
        ForeignKey("analytics_modules.id", ondelete="CASCADE"), index=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"),
                                           index=True)
    options: Mapped[dict | None] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    module: Mapped[AnalyticsModule] = relationship(back_populates="assignments")

    __table_args__ = (UniqueConstraint("module_id", "camera_id", name="uq_module_camera"),)


class Recording(Base):
    """코어가 소유하는 영상 자산 — 주로 이벤트 클립.

    상시 녹화 세그먼트는 여기 넣지 않는다. 수만 개로 계속 회전하므로 진실의 원천을
    미디어 서버에 두고 타임라인을 그릴 때 물어보는 편이 낫다.

    주의: 이벤트 클립은 **반드시 별도 파일로 복사해** 온다. 세그먼트에 참조만 걸어 두면
      보존기간이 지나 회전 삭제될 때 사고 영상이 같이 사라진다. 상시 녹화는 며칠,
      사고 클립은 길게 — 보존 정책이 다르기 때문이다.
    """

    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"),
                                           index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"),
                                                 index=True)
    kind: Mapped[str] = mapped_column(String(16), default="event")   # event | manual
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_sec: Mapped[float] = mapped_column(Float, default=0.0)
    path: Mapped[str] = mapped_column(String(400), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboundTarget(Base):
    """아웃바운드 대상 — 인바운드 바인딩의 대칭.

    인바운드가 '밖의 아무 모양 -> 우리 정규형' 이면, 여기는 '우리 이벤트 -> 상대가 원하는 모양'
    이다. 상대를 우리 형식에 맞추라고 할 수 없으므로 템플릿을 데이터로 들고 있는다.

    지금 구현은 kind="mqtt" 뿐이다. 웹훅·알림·PLC 는 kind 를 늘리고 sender 함수를 추가하면
    붙는다 — 코어는 고치지 않는다(매니페스토 3번).
    """

    __tablename__ = "outbound_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    kind: Mapped[str] = mapped_column(String(16), default="mqtt")

    # ── 어떤 이벤트를 보낼지. 비우면 전부. ───────────────────────────
    solution_codes: Mapped[list | None] = mapped_column(JSON)
    camera_ids: Mapped[list | None] = mapped_column(JSON)

    # ── kind 별 설정. mqtt: {"topic_template":..., "qos":0, "retain":false} ──
    config: Mapped[dict | None] = mapped_column(JSON)
    # 상대가 원하는 페이로드 모양. 중괄호 치환({event.code} 등).
    payload_template: Mapped[str] = mapped_column(Text, default="")

    # ── 재시도 정책 ─────────────────────────────────────────────────
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    retry_backoff_sec: Mapped[float] = mapped_column(Float, default=5.0)

    # ── 진단 ────────────────────────────────────────────────────────
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(300), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboundDelivery(Base):
    """발송 대기·이력 (outbox 패턴).

    메모리 큐가 아니라 테이블인 이유가 둘이다.
      1. 수신 측이 죽어 있어도 이벤트를 잃지 않는다.
      2. **서버를 재시작해도 대기분이 살아남는다.** 메모리 큐면 통째로 사라진다.

    '왜 안 갔나' 를 어드민이 볼 수 있어야 하므로 시도 횟수와 실패 사유를 남긴다.
    """

    __tablename__ = "outbound_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("outbound_targets.id", ondelete="CASCADE"),
                                           index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"),
                                                 index=True)
    # pending: 아직 안 보냄 / sent: 성공 / failed: 실패했고 재시도 예정 / expired: 최대 시도 초과
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    # 발송 시점에 만들어 둔다. 이벤트가 지워져도 무엇을 보내려 했는지 남는다.
    topic: Mapped[str] = mapped_column(String(400), default="")
    payload: Mapped[str] = mapped_column(Text, default="")

    error: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MqttMessage(Base):
    """수신 원문 로그. 사후 분석용. 보존기간이 지나면 정리된다.

    (화면의 'MQTT 로그' 탭은 브로커에 직결 구독하므로 이 테이블을 쓰지 않는다.
     여기 쌓는 목적은 '그때 무슨 메시지가 왔었나'를 나중에 되짚기 위해서다.)
    """

    __tablename__ = "mqtt_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    topic: Mapped[str] = mapped_column(String(400), index=True)
    payload: Mapped[str] = mapped_column(Text, default="")
    camera_id: Mapped[int | None] = mapped_column(ForeignKey("cameras.id", ondelete="SET NULL"))
    matched: Mapped[bool] = mapped_column(Boolean, default=False)   # 탐지규칙에 걸렸는지


class Setting(Base):
    """운영 설정 key/value. (위험도 매핑, 보존정책 등)"""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

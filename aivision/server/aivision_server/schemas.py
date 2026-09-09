"""API DTO.

프론트가 쓰는 계약이다.

지금 단계에서 일부러 넣지 않은 것
  · 심각도·처리 상태 같은 분류. 기준이 정해지지 않았다. 미리 만들어 두면 화면에 근거 없는
    값이 뜨고, 진짜 기준이 정해졌을 때 그것부터 지워야 한다.
  · 탐지 항목(솔루션) 매핑을 카메라 등록 단계에서 묻는 것. 등록에 필요한 것은 IP 뿐이다.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ────────────────────────────────────────────────────────────── 탐지 항목

class SolutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    short_name: str
    description: str
    event_type: str
    color: str
    sort_order: int
    enabled: bool


class SolutionPatch(BaseModel):
    name: str | None = None
    short_name: str | None = None
    description: str | None = None
    event_type: str | None = None
    color: str | None = None
    enabled: bool | None = None


# ────────────────────────────────────────────────────────────── 카메라

class CameraOut(BaseModel):
    """목록 화면이 쓰는 카메라 DTO. 비밀번호는 절대 나가지 않는다."""

    id: int
    name: str
    location: str
    note: str
    ip: str
    rtsp_port: int
    rtsp_path: str
    username: str
    has_password: bool
    mac: str
    vendor: str
    model: str
    enabled: bool
    status: str                    # "normal" | "offline"  — 카메라 연결 상태(이벤트와 무관)
    last_seen_at: datetime | None
    last_error: str
    sols: list[str]
    detection_source: str
    stream_url: str                # MJPEG 주소 (브라우저용)
    rtsp_url: str = ""             # 분석 모듈이 영상을 가져가는 곳 (미디어 서버 경유)
    record_enabled: bool = False
    record_retention_days: int = 3
    today: int = 0                 # 금일 이벤트 수
    total: int = 0                 # 누적 이벤트 수


class CameraCreate(BaseModel):
    """등록에 꼭 필요한 것은 IP 뿐이다.

    이름·설치 위치는 비워 두면 IP 로 채운다. 현장에서 카메라를 꽂자마자 화면에 띄우는 것이
    먼저고, 이름은 나중에 붙여도 된다.
    탐지 항목 매핑은 등록 단계에서 묻지 않는다 — 무엇을 볼지 정해진 뒤에 연결한다.
    """

    ip: str = Field(min_length=3, max_length=45)
    name: str = Field(default="", max_length=64)
    location: str = Field(default="", max_length=120)
    rtsp_port: int = 554
    rtsp_path: str = "/profile2/media.smp"
    username: str = ""
    password: str = ""
    mac: str = ""
    vendor: str = "HANWHA"
    model: str = ""
    note: str = ""
    enabled: bool = True
    record_enabled: bool = False
    record_retention_days: int = 3
    sols: list[str] = Field(default_factory=list)

    @field_validator("mac")
    @classmethod
    def _norm_mac(cls, v: str) -> str:
        """콜론/하이픈 어느 쪽으로 넣어도 대문자 콜론 표기로 통일한다.
        MQTT 토픽 접두와 글자 단위로 일치해야 하기 때문이다."""
        return (v or "").strip().upper().replace("-", ":")


class CameraPatch(BaseModel):
    name: str | None = None
    location: str | None = None
    ip: str | None = None
    rtsp_port: int | None = None
    rtsp_path: str | None = None
    username: str | None = None
    password: str | None = None       # 빈 문자열이면 '변경 없음'으로 취급
    mac: str | None = None
    vendor: str | None = None
    model: str | None = None
    note: str | None = None
    enabled: bool | None = None
    record_enabled: bool | None = None
    record_retention_days: int | None = None
    sols: list[str] | None = None

    @field_validator("mac")
    @classmethod
    def _norm_mac(cls, v: str | None) -> str | None:
        return None if v is None else v.strip().upper().replace("-", ":")


class CameraTestResult(BaseModel):
    ok: bool
    rtsp_ok: bool
    detail: str
    width: int = 0
    height: int = 0
    elapsed_ms: float = 0.0


# ────────────────────────────────────────────────────────────── 인바운드 바인딩

class BindingBase(BaseModel):
    """들어온 메시지를 내부 신호로 옮기는 규칙. 표현식 문법은 mqtt/mapping.py 참조."""

    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    transport: str = "mqtt"                 # mqtt | http
    payload_profile: str = "raw"            # raw | onvif
    priority: int = 100
    live_only: bool = False

    topic_pattern: str = ""                 # MQTT 와일드카드. "+/fireAlarm", "aivision/detect/#"
    payload_filter: dict[str, str] | None = None

    camera_from: str = "topic_mac"          # topic_mac | topic_segment | payload | fixed
    camera_expr: str = ""
    camera_id: int | None = None

    item_from: str = "fixed"                # fixed | payload
    item_expr: str = ""
    solution_code: str | None = None

    state_expr: str = ""                    # 비우면 '수신 자체가 발생'
    state_active: str = "active"
    state_inactive: str = "inactive"

    module_expr: str = ""                   # 무엇이 판정했나. 예 "$.module_id". 비워도 됨
    confidence_expr: str = ""
    ts_expr: str = ""
    boxes_expr: str = ""
    boxes_format: str = "xyxy_norm"         # xyxy_norm | xyxy_px | xywh_px | cxcywh_norm


class BindingCreate(BindingBase):
    pass


class BindingPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    transport: str | None = None
    payload_profile: str | None = None
    priority: int | None = None
    live_only: bool | None = None
    topic_pattern: str | None = None
    payload_filter: dict[str, str] | None = None
    camera_from: str | None = None
    camera_expr: str | None = None
    camera_id: int | None = None
    item_from: str | None = None
    item_expr: str | None = None
    solution_code: str | None = None
    state_expr: str | None = None
    state_active: str | None = None
    state_inactive: str | None = None
    module_expr: str | None = None
    confidence_expr: str | None = None
    ts_expr: str | None = None
    boxes_expr: str | None = None
    boxes_format: str | None = None


class BindingOut(BindingBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    last_matched_at: datetime | None = None
    match_count: int = 0


class BindingTestRequest(BaseModel):
    """어드민 '시험' — 실제 토픽·페이로드를 넣어 어느 바인딩이 걸리는지 본다."""

    topic: str
    payload: str = ""                       # 원문 문자열(JSON 또는 빈 값)
    transport: str = "mqtt"


class BindingTestResult(BaseModel):
    binding_id: int
    binding_name: str
    matched: bool
    reason: str = ""
    camera_id: int | None = None
    item: str | None = None
    state: str | None = None
    boxes: int = 0


# ────────────────────────────────────────────────────────────── 분석 모듈

class ModuleRegister(BaseModel):
    """모듈이 자기를 등록할 때 보내는 것. 같은 id 로 다시 부르면 갱신된다."""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=80)
    # internal(서버 안) | sidecar(같은 PC 다른 컨테이너) | remote(다른 PC) | edge(카메라 안)
    # 코어는 이 값으로 동작을 바꾸지 않는다. 화면에 보여 주려고 받아 둔다.
    kind: str = "external"
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    endpoint: str = ""


class AssignmentCreate(BaseModel):
    camera_id: int
    # ROI·스케줄·임계값 등. 코어는 해석하지 않고 모듈에 그대로 넘긴다.
    options: dict = Field(default_factory=dict)
    enabled: bool = True


class AssignmentOut(BaseModel):
    id: int
    module_id: str
    camera_id: int
    options: dict = Field(default_factory=dict)
    enabled: bool


class ModuleOut(BaseModel):
    id: str
    name: str
    kind: str
    description: str
    capabilities: list[str] = Field(default_factory=list)
    endpoint: str
    enabled: bool
    last_seen_at: datetime | None = None
    alive: bool = False
    last_status: dict = Field(default_factory=dict)
    assignments: list[AssignmentOut] = Field(default_factory=list)


class ModuleWorkItem(BaseModel):
    """모듈에게 넘기는 '볼 것' 하나. 카메라 IP·계정은 알려 주지 않는다."""

    camera_id: int
    camera_name: str
    location: str
    rtsp: str                     # 미디어 서버에서 가져가는 주소
    snapshot: str
    options: dict = Field(default_factory=dict)


class ModuleWork(BaseModel):
    module_id: str
    items: list[ModuleWorkItem] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────── 이벤트

class BoxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    x1: float
    y1: float
    x2: float
    y2: float
    label: str
    score: float


class SegmentOut(BaseModel):
    """녹화 세그먼트 한 조각. 타임라인용."""

    start: datetime
    duration_sec: float
    url: str = ""


class EventOut(BaseModel):
    id: str                       # EVT-00001
    ts: datetime
    cam: int
    cam_name: str
    cam_location: str
    sol: str
    type: str
    source: str
    module: str = ""              # 무엇이 판정했나 (바인딩이 뽑아 준 경우에만)
    confidence: float | None
    has_snapshot: bool
    has_clip: bool = False
    boxes: list[BoxOut] = Field(default_factory=list)


class EventPage(BaseModel):
    items: list[EventOut]
    total: int
    limit: int
    offset: int


# ────────────────────────────────────────────────────────────── 아웃바운드

class OutboundTargetBase(BaseModel):
    """이벤트를 밖으로 내보낼 대상. 인바운드 바인딩의 대칭이다."""

    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    kind: str = "mqtt"                    # 지금은 mqtt 만. 웹훅·알림·PLC 는 나중에 붙는다.
    # 비우면 전부 보낸다.
    solution_codes: list[str] = Field(default_factory=list)
    camera_ids: list[int] = Field(default_factory=list)
    # kind 별 설정. mqtt: {"topic_template": "...", "qos": 0, "retain": false}
    config: dict = Field(default_factory=dict)
    payload_template: str = ""
    max_attempts: int = 5
    retry_backoff_sec: float = 5.0


class OutboundTargetCreate(OutboundTargetBase):
    pass


class OutboundTargetPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    kind: str | None = None
    solution_codes: list[str] | None = None
    camera_ids: list[int] | None = None
    config: dict | None = None
    payload_template: str | None = None
    max_attempts: int | None = None
    retry_backoff_sec: float | None = None


class OutboundTargetOut(OutboundTargetBase):
    id: int
    last_sent_at: datetime | None = None
    sent_count: int = 0
    fail_count: int = 0
    last_error: str = ""


class OutboundTestResult(BaseModel):
    ok: bool
    event: str
    topic: str
    payload: str
    detail: str = ""


# ────────────────────────────────────────────────────────────── 통계

class SeriesPoint(BaseModel):
    label: str
    key: str                      # 원본 구간 키(정렬·조회용)
    count: int


class SolutionCount(BaseModel):
    code: str
    short_name: str
    color: str
    count: int


class CameraCount(BaseModel):
    camera_id: int
    name: str
    location: str
    count: int


class StatsOut(BaseModel):
    bucket: str                   # hourly | daily | weekly | monthly
    start: datetime
    end: datetime
    total: int
    series: list[SeriesPoint]
    by_solution: list[SolutionCount]
    by_camera: list[CameraCount]


class SummaryOut(BaseModel):
    """대시보드 상단 KPI."""

    cameras_total: int
    cameras_normal: int
    cameras_offline: int
    events_today: int


# ────────────────────────────────────────────────────────────── 설정

class SettingsOut(BaseModel):
    mqtt_ws_url: str                      # 브라우저가 직결 구독할 브로커 주소
    # 수신 원문을 DB 에 남기는 정책. 기본은 남기지 않는다(off).
    mqtt_log_mode: str                    # off | unmatched | all
    mqtt_log_topics: str                  # 특정 채널만 남길 때. 채우면 mode 보다 우선
    mqtt_log_retention_days: int
    snapshot_on_event: bool
    event_dedup_sec: float


class SettingsPatch(BaseModel):
    mqtt_log_mode: str | None = None
    mqtt_log_topics: str | None = None
    mqtt_log_retention_days: int | None = None
    snapshot_on_event: bool | None = None
    event_dedup_sec: float | None = None

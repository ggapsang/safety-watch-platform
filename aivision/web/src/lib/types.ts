/** 서버 DTO 타입. aivision_server/schemas.py 와 1:1 로 대응한다. */

export type Bucket = "hourly" | "daily" | "weekly" | "monthly";

export interface Solution {
  code: string;
  name: string;
  short_name: string;
  description: string;
  event_type: string;
  color: string;
  sort_order: number;
  enabled: boolean;
}

export interface Camera {
  id: number;
  name: string;
  location: string;
  note: string;
  ip: string;
  rtsp_port: number;
  rtsp_path: string;
  /** 보조(저화질) 스트림 경로. 비우면 없음 */
  rtsp_path_sub: string;
  username: string;
  has_password: boolean;
  mac: string;
  vendor: string;
  model: string;
  enabled: boolean;
  status: "normal" | "offline";
  last_seen_at: string | null;
  last_error: string;
  sols: string[];
  detection_source: string;
  stream_url: string;
  rtsp_url: string;
  record_enabled: boolean;
  record_retention_hours: number;
  /** 화면에 늘어놓는 순서 (작을수록 앞). 스트립에서 끌어 바꾼다 */
  sort_order: number;
  today: number;
  total: number;
}

/** 등록·수정 폼이 보내는 값. IP 외에는 전부 선택 입력이다.
 *  탐지 항목(sols)·MAC 은 아직 화면에서 다루지 않는다 — 항목이 정해지면 추가한다. */
export interface CameraInput {
  ip: string;
  name: string;
  location: string;
  rtsp_port: number;
  rtsp_path: string;
  rtsp_path_sub: string;
  username: string;
  password: string;
  note: string;
  enabled: boolean;
  record_enabled: boolean;
  record_retention_hours: number;
}

export interface CameraTestResult {
  ok: boolean;
  rtsp_ok: boolean;
  detail: string;
  width: number;
  height: number;
  elapsed_ms: number;
}

export interface Box {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  label: string;
  score: number;
}

export interface SafetyEvent {
  id: string;
  ts: string;
  cam: number;
  cam_name: string;
  cam_location: string;
  sol: string;
  type: string;
  source: string;
  module: string;
  confidence: number | null;
  has_snapshot: boolean;
  has_clip: boolean;
  boxes: Box[];
}

export interface EventPage {
  items: SafetyEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface Summary {
  cameras_total: number;
  cameras_normal: number;
  cameras_offline: number;
  events_today: number;
  /** 오늘 포함 7일 / 30일 창. '이번 주' 로 세면 월요일마다 숫자가 튄다 */
  events_week: number;
  events_month: number;
}

export interface SeriesPoint {
  label: string;
  key: string;
  count: number;
}

export interface Stats {
  bucket: Bucket;
  start: string;
  end: string;
  total: number;
  series: SeriesPoint[];
  by_solution: { code: string; short_name: string; color: string; count: number }[];
  by_camera: { camera_id: number; name: string; location: string; count: number }[];
}

/** 인바운드 바인딩 — 들어온 메시지를 내부 신호로 옮기는 규칙.
 *  '무엇이 걸리는가(match)' 와 '어떻게 뽑는가(extract)' 로 나뉜다. */
export interface Binding {
  id: number;
  name: string;
  enabled: boolean;
  transport: "mqtt" | "http";
  payload_profile: "raw" | "onvif";
  priority: number;
  live_only: boolean;

  topic_pattern: string;
  payload_filter: Record<string, string> | null;

  camera_from: "topic_mac" | "topic_segment" | "payload" | "fixed";
  camera_expr: string;
  camera_id: number | null;

  item_from: "fixed" | "payload";
  item_expr: string;
  solution_code: string | null;

  state_expr: string;
  state_active: string;
  state_inactive: string;

  module_expr: string;
  confidence_expr: string;
  ts_expr: string;
  boxes_expr: string;
  boxes_format: "xyxy_norm" | "xyxy_px" | "xywh_px" | "cxcywh_norm";

  last_matched_at: string | null;
  match_count: number;
}

export type BindingInput = Omit<Binding, "id" | "last_matched_at" | "match_count">;

export interface BindingTestResult {
  binding_id: number;
  binding_name: string;
  matched: boolean;
  reason: string;
  camera_id: number | null;
  item: string | null;
  state: string | null;
  boxes: number;
}

/** 아웃바운드 대상 — 인바운드 바인딩의 대칭. */
export interface OutboundTarget {
  id: number;
  name: string;
  enabled: boolean;
  kind: string;
  solution_codes: string[];
  camera_ids: number[];
  config: Record<string, unknown>;
  payload_template: string;
  max_attempts: number;
  retry_backoff_sec: number;
  last_sent_at: string | null;
  sent_count: number;
  fail_count: number;
  last_error: string;
}

export type OutboundTargetInput = Omit<
  OutboundTarget,
  "id" | "last_sent_at" | "sent_count" | "fail_count" | "last_error"
>;

export interface OutboundTestResult {
  ok: boolean;
  event: string;
  topic: string;
  payload: string;
  detail: string;
}

export interface OutboundDelivery {
  id: number;
  target_id: number;
  event: string | null;
  status: "pending" | "sent" | "failed" | "expired";
  attempt: number;
  topic: string;
  error: string;
  created_at: string;
  sent_at: string | null;
  next_attempt_at: string | null;
}

export interface AppSettings {
  mqtt_ws_url: string;
  /** 수신 원문을 DB 에 남기는 정책. 기본은 off — 실시간 화면은 브로커 직결이라 영향 없음 */
  mqtt_log_mode: "off" | "unmatched" | "all";
  /** 특정 채널만 남길 때. 채우면 mode 보다 우선한다 */
  mqtt_log_topics: string;
  mqtt_log_retention_days: number;
  /** 상시 녹화 전체 용량 상한(GB). 0 이면 제한 없음 */
  record_max_gb: number;
  snapshot_on_event: boolean;
  event_dedup_sec: number;
}

/** 분석 모듈. 코어는 모듈이 무엇을 하는지 모른다 — 이름과 살아 있는지, 그리고
 *  자기 화면이 있으면 그 주소만 안다. */
export interface AnalyticsModule {
  id: string;
  name: string;
  kind: string;
  description: string;
  capabilities: string[];
  /** 모듈이 자기 화면을 들고 있을 때 그 주소. 있으면 사이드바에 탭이 생긴다 */
  endpoint: string;
  enabled: boolean;
  alive: boolean;
  last_seen_at: string | null;
  assignments: { id: number; camera_id: number; enabled: boolean }[];
}

export interface SystemStatus {
  detection_sources: {
    name: string;
    running: boolean;
    last_error: string;
    connected?: boolean;
    received?: number;
    matched?: number;
    cameras?: number;
    rules?: number;
  }[];
  streams: Record<
    string,
    {
      camera_id: number;
      connected: boolean;
      reconnects: number;
      fps: number;
      width: number;
      height: number;
      stale_sec: number;
      last_error: string;
    }
  >;
  mqtt: {
    host: string;
    port: number;
    ws_url: string;
    subscribe: string;
    messages_1h: number;
  };
  stream: { fps: number; jpeg_quality: number; max_width: number };
  /** 상시 녹화가 지금 얼마나 차 있나 */
  record?: {
    bytes: number;
    gb: number;
    files: number;
    limit_gb: number;
    over: boolean;
    disk_free_gb: number;
    /** 지금 어디에 쌓이나. 도커 볼륨 이름이거나 호스트 폴더 경로 */
    location: string;
  };
}

/** 서버 → 브라우저 푸시 (WebSocket) */
export type PushMessage =
  | { kind: "hello"; data: { ok: boolean } }
  | { kind: "ping"; data: Record<string, never> }
  | { kind: "event"; data: SafetyEvent }
  | { kind: "camera-status"; data: { camera_id: number; name: string; location: string; status: string; last_error: string } }
  | { kind: "cameras-changed"; data: Record<string, never> }
  | {
      kind: "live-boxes";
      data: { camera_id: number; item: string; ts: string; boxes: Box[] };
    };

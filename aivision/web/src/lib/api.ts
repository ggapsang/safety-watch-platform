/** REST 클라이언트.
 *
 * 서버와 같은 오리진에서 서빙되므로 baseURL 이 없다(개발 시에는 Vite 프록시가 /api 를 넘긴다).
 * 에러는 서버가 준 detail 문자열을 그대로 던진다 — 화면이 사람 말로 된 메시지를 보여줄 수 있게.
 */
import type {
  AppSettings,
  Bucket,
  Camera,
  CameraInput,
  CameraTestResult,
  Binding,
  BindingInput,
  BindingTestResult,
  EventPage,
  OutboundDelivery,
  OutboundTarget,
  OutboundTargetInput,
  OutboundTestResult,
  SafetyEvent,
  Solution,
  Stats,
  Summary,
  SystemStatus,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  if (!res.ok) {
    let detail = `요청 실패 (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail)) detail = body.detail[0]?.msg ?? detail;
    } catch {
      /* 본문이 JSON 이 아니면 기본 메시지를 쓴다 */
    }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const qs = (params: Record<string, unknown>): string => {
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => sp.append(key, String(v)));
    else sp.append(key, String(value));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
};

export interface EventQuery {
  start?: string;
  end?: string;
  cameras?: number[];
  solutions?: string[];
  q?: string;
  limit?: number;
  offset?: number;
}

export const api = {
  // ── 카메라 ────────────────────────────────────────────────────────
  cameras: () => request<Camera[]>("/api/cameras"),
  createCamera: (body: CameraInput) =>
    request<Camera>("/api/cameras", { method: "POST", body: JSON.stringify(body) }),
  patchCamera: (id: number, body: Partial<CameraInput>) =>
    request<Camera>(`/api/cameras/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteCamera: (id: number) => request<void>(`/api/cameras/${id}`, { method: "DELETE" }),
  testCamera: (id: number) =>
    request<CameraTestResult>(`/api/cameras/${id}/test`, { method: "POST" }),
  testEndpoint: (body: CameraInput) =>
    request<CameraTestResult>("/api/cameras/test", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // ── 이벤트 ────────────────────────────────────────────────────────
  events: (query: EventQuery = {}) => request<EventPage>(`/api/events${qs({ ...query })}`),
  event: (code: string) => request<SafetyEvent>(`/api/events/${code}`),
  summary: () => request<Summary>("/api/events/summary"),
  exportUrl: (query: EventQuery = {}) => `/api/events/export.csv${qs({ ...query })}`,
  snapshotUrl: (code: string) => `/api/events/${code}/snapshot`,
  clipUrl: (code: string) => `/api/events/${code}/clip`,

  // ── 통계 ──────────────────────────────────────────────────────────
  stats: (query: { bucket: Bucket } & Omit<EventQuery, "limit" | "offset" | "q">) =>
    request<Stats>(`/api/stats${qs({ ...query })}`),

  // ── 어드민 ────────────────────────────────────────────────────────
  solutions: () => request<Solution[]>("/api/solutions"),
  patchSolution: (code: string, body: Partial<Solution>) =>
    request<Solution>(`/api/solutions/${code}`, { method: "PATCH", body: JSON.stringify(body) }),
  bindings: () => request<Binding[]>("/api/bindings"),
  createBinding: (body: BindingInput) =>
    request<Binding>("/api/bindings", { method: "POST", body: JSON.stringify(body) }),
  patchBinding: (id: number, body: Partial<BindingInput>) =>
    request<Binding>(`/api/bindings/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteBinding: (id: number) => request<void>(`/api/bindings/${id}`, { method: "DELETE" }),
  testBinding: (topic: string, payload: string) =>
    request<BindingTestResult[]>("/api/bindings/test", {
      method: "POST",
      body: JSON.stringify({ topic, payload, transport: "mqtt" }),
    }),
  settings: () => request<AppSettings>("/api/settings"),
  saveSettings: (body: Partial<AppSettings>) =>
    request<AppSettings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  system: () => request<SystemStatus>("/api/system"),

  // ── 아웃바운드 ────────────────────────────────────────────────────
  outboundTargets: () => request<OutboundTarget[]>("/api/outbound/targets"),
  createOutboundTarget: (body: OutboundTargetInput) =>
    request<OutboundTarget>("/api/outbound/targets", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  patchOutboundTarget: (id: number, body: Partial<OutboundTargetInput>) =>
    request<OutboundTarget>(`/api/outbound/targets/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteOutboundTarget: (id: number) =>
    request<void>(`/api/outbound/targets/${id}`, { method: "DELETE" }),
  testOutboundTarget: (id: number) =>
    request<OutboundTestResult>(`/api/outbound/targets/${id}/test`, { method: "POST" }),
  outboundDeliveries: (targetId?: number) =>
    request<OutboundDelivery[]>(`/api/outbound/deliveries${qs({ target_id: targetId })}`),
  outboundFields: () => request<string[]>("/api/outbound/fields"),
  outboundDrain: () =>
    request<{ handled: number; pending: number }>("/api/outbound/drain", { method: "POST" }),
  purgeMqttLog: (days = 0) =>
    request<{ deleted: number; kept_days: number }>(
      `/api/system/mqtt-log/purge${qs({ days })}`,
      { method: "POST" },
    ),

  // ── 영상 ──────────────────────────────────────────────────────────
  streamUrl: (cameraId: number) => `/api/stream/${cameraId}`,
  stillUrl: (cameraId: number) => `/api/stream/${cameraId}/snapshot.jpg`,
};

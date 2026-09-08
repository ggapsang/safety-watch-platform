/** MQTT 로그 — 브로커에 **직결 구독**해 원문을 그대로 본다.
 *
 * 서버를 거치지 않는 이유: 카메라가 실제로 무엇을 보냈는지 확인하는 화면이라, 중간에서
 * 가공·필터되면 목적을 잃는다. 토픽에 콜론·앰퍼샌드 같은 문자가 섞여 있어도 원문 그대로 보인다.
 * (서버가 적재해 둔 과거 로그는 /api/mqtt-log 로 따로 조회한다.)
 */
import mqtt, { type MqttClient } from "mqtt";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { IconClose } from "../components/icons";
import { Button, Card, Dot, Field, Input, Section, cx } from "../components/ui";
import { useSettings } from "../lib/hooks";

interface Row {
  seq: number;
  ts: Date;
  topic: string;
  payload: string;
}

const MAX_ROWS = 500;
type Conn = "connecting" | "connected" | "closed" | "error";

const CONN_LABEL: Record<Conn, string> = {
  connecting: "연결 중",
  connected: "연결됨",
  closed: "끊김",
  error: "오류",
};

/** 토픽에서 MAC 접두를 떼어 '형태'만 남긴다 — 장비가 여러 대여도 종류별로 묶여 보인다. */
const shapeOf = (topic: string): string =>
  topic.replace(/^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}/, "{MAC}");

const pretty = (payload: string): string => {
  if (!payload.trim()) return "(빈 페이로드)";
  try {
    return JSON.stringify(JSON.parse(payload), null, 2);
  } catch {
    return payload;
  }
};

export function MqttLog() {
  const navigate = useNavigate();
  const { data: settings } = useSettings();
  const [conn, setConn] = useState<Conn>("connecting");
  const [error, setError] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [paused, setPaused] = useState(false);
  const [filter, setFilter] = useState("");
  const [shapes, setShapes] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Row | null>(null);

  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const seq = useRef(0);
  const clientRef = useRef<MqttClient | null>(null);

  const wsUrl = settings?.mqtt_ws_url;

  useEffect(() => {
    if (!wsUrl) return;
    setConn("connecting");
    setError("");

    const client = mqtt.connect(wsUrl, {
      reconnectPeriod: 4000,
      connectTimeout: 8000,
      clientId: `aivision-ui-${Math.random().toString(16).slice(2, 8)}`,
    });
    clientRef.current = client;

    client.on("connect", () => {
      setConn("connected");
      setError("");
      client.subscribe("#", { qos: 0 });
    });
    client.on("reconnect", () => setConn("connecting"));
    client.on("close", () => setConn("closed"));
    client.on("error", (err) => {
      setConn("error");
      setError(err?.message ?? "브로커에 연결할 수 없습니다.");
    });
    client.on("message", (topic, payload) => {
      if (pausedRef.current) return;
      const row: Row = {
        seq: ++seq.current,
        ts: new Date(),
        topic,
        payload: payload.toString(),
      };
      setRows((prev) => [row, ...prev].slice(0, MAX_ROWS));
      setShapes((prev) => {
        const key = shapeOf(topic);
        return key in prev ? prev : { ...prev, [key]: true };
      });
    });

    return () => {
      client.end(true);
      clientRef.current = null;
    };
  }, [wsUrl]);

  const shapeKeys = useMemo(() => Object.keys(shapes).sort(), [shapes]);

  const shown = rows.filter((r) => {
    if (!shapes[shapeOf(r.topic)]) return false;
    if (!filter.trim()) return true;
    const needle = filter.trim().toLowerCase();
    return r.topic.toLowerCase().includes(needle) || r.payload.toLowerCase().includes(needle);
  });

  const setAllShapes = (value: boolean) =>
    setShapes(Object.fromEntries(shapeKeys.map((k) => [k, value])));

  return (
    <>
      <Section
        title="브로커 직결 구독"
        desc={wsUrl ? `${wsUrl} · 토픽 '#' 전체 구독` : "브로커 주소를 불러오는 중…"}
        actions={
          <>
            <span className="inline-flex items-center gap-2 rounded-full border border-hairline bg-canvas px-[13px] py-[5px] text-[12.5px] text-muted">
              <Dot ok={conn === "connected"} />
              {CONN_LABEL[conn]}
            </span>
            <Button size="sm" onClick={() => setPaused((p) => !p)}>
              {paused ? "재개" : "일시정지"}
            </Button>
            <Button size="sm" onClick={() => setRows([])}>
              비우기
            </Button>
          </>
        }
      >
        {error && (
          <Card className="mb-4 border-error/30 bg-error/5 text-[12.5px] text-error">
            {error} — 브라우저가 브로커의 WebSocket 포트(9001)에 직접 닿아야 합니다. 다른 PC 에서
            접속 중이라면 .env 의 MQTT_WS_URL 을 서버 PC 의 IP 로 바꾸세요.
          </Card>
        )}

        <Card className="grid gap-4">
          <Field label="토픽 · 페이로드 검색">
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="토픽 필터…"
            />
          </Field>

          <div>
            <div className="mb-[9px] flex items-center gap-3">
              <span className="text-[11.5px] font-medium text-muted">토픽 형태</span>
              <button
                type="button"
                onClick={() => setAllShapes(true)}
                className="text-[11.5px] text-primary hover:text-primary-active"
              >
                전체선택
              </button>
              <button
                type="button"
                onClick={() => setAllShapes(false)}
                className="text-[11.5px] text-primary hover:text-primary-active"
              >
                전체해제
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              {shapeKeys.length === 0 ? (
                <span className="text-[12.5px] text-muted-soft">발견된 토픽이 없습니다.</span>
              ) : (
                shapeKeys.map((key) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setShapes((prev) => ({ ...prev, [key]: !prev[key] }))}
                    className={cx(
                      "rounded-full border px-[13px] py-[5px] font-mono text-[11.5px] transition-colors",
                      shapes[key]
                        ? "border-primary bg-primary-soft text-primary-active"
                        : "border-hairline bg-canvas text-muted-soft",
                    )}
                  >
                    {key}
                  </button>
                ))
              )}
            </div>
          </div>
        </Card>
      </Section>

      <Section title="수신 메시지" desc={`${shown.length}건 표시 · 최대 ${MAX_ROWS}건 보관`}>
        <Card padded={false}>
          <div className="max-h-[560px] overflow-y-auto">
            <table className="w-full border-collapse text-[12.5px]">
              <thead className="sticky top-0 bg-canvas">
                <tr>
                  {["수신시각", "토픽", "페이로드"].map((h) => (
                    <th
                      key={h}
                      className="border-b border-hairline px-[14px] py-3 text-left text-[11.5px] font-semibold text-muted"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="px-4 py-12 text-center text-muted-soft">
                      수신된 메시지가 없습니다.
                    </td>
                  </tr>
                ) : (
                  shown.map((r) => (
                    <tr
                      key={r.seq}
                      onClick={() => setSelected(r)}
                      className="cursor-pointer transition-colors hover:bg-surface-soft"
                    >
                      <td className="tnum whitespace-nowrap border-b border-hairline/60 px-[14px] py-[9px] text-muted">
                        {r.ts.toLocaleTimeString("ko-KR", { hour12: false })}
                      </td>
                      <td className="max-w-[420px] truncate border-b border-hairline/60 px-[14px] py-[9px] font-mono text-body-strong">
                        {r.topic}
                      </td>
                      <td className="max-w-[520px] truncate border-b border-hairline/60 px-[14px] py-[9px] font-mono text-muted">
                        {r.payload || "—"}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </Section>

      {selected && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(20,20,19,.45)] p-4"
          onClick={() => setSelected(null)}
          role="presentation"
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="w-[min(760px,94vw)] overflow-hidden rounded-xl border border-hairline bg-canvas shadow-[0_18px_50px_rgba(20,20,19,.18)]"
          >
            <div className="flex items-center justify-between border-b border-hairline px-[22px] py-[15px]">
              <h3 className="truncate font-mono text-[13px] text-ink">{selected.topic}</h3>
              <button
                type="button"
                onClick={() => setSelected(null)}
                className="text-muted-soft hover:text-ink"
                aria-label="닫기"
              >
                <IconClose />
              </button>
            </div>
            <div className="px-[22px] py-[18px]">
              <div className="mb-2 text-[11.5px] font-medium text-muted">원문 페이로드</div>
              <pre className="max-h-[50vh] overflow-auto rounded-lg border border-hairline bg-surface-soft p-4 font-mono text-[12px] leading-relaxed text-body">
                {pretty(selected.payload)}
              </pre>
            </div>
            <div className="flex items-center gap-2 border-t border-hairline px-[22px] py-[14px]">
              <span className="text-[12px] text-muted">
                이 메시지를 이벤트로 만들려면 바인딩이 필요합니다.
              </span>
              <div className="flex-1" />
              <Button onClick={() => navigator.clipboard?.writeText(selected.payload)}>복사</Button>
              <Button
                variant="primary"
                onClick={() =>
                  navigate("/admin", {
                    state: { tab: "bindings", topic: selected.topic, payload: selected.payload },
                  })
                }
              >
                바인딩 만들기
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

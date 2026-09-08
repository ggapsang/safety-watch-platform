/** 아웃바운드 관리 — 이벤트를 밖으로 내보내는 대상.
 *
 * 인바운드 바인딩의 대칭이라 화면 구조도 같게 뒀다(목록 + 폼 + 시험).
 * 다른 점은 **전달 이력**이다. 발송은 실패할 수 있고, 실패를 눈으로 확인하지 못하면
 * 조용히 안 되는 기능이 된다.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  Button,
  Card,
  Checkbox,
  Dot,
  EmptyRow,
  ErrorText,
  Field,
  Input,
  Modal,
  Section,
  Select,
  Table,
  Td,
  Textarea,
  cx,
} from "../components/ui";
import { api, ApiError } from "../lib/api";
import { fmtAgo, fmtDateTime } from "../lib/format";
import { useCameras, useSolutions } from "../lib/hooks";
import type { OutboundTarget, OutboundTargetInput, OutboundTestResult } from "../lib/types";

const DEFAULT_TOPIC = "aivision/event/{event.code}";
const DEFAULT_PAYLOAD = `{
  "event": "{event.code}",
  "ts": "{event.ts}",
  "camera": {camera.id},
  "location": "{camera.location}",
  "item": "{item.code}",
  "type": "{event.type}",
  "boxes": {boxes_json}
}`;

const EMPTY: OutboundTargetInput = {
  name: "",
  enabled: true,
  kind: "mqtt",
  solution_codes: [],
  camera_ids: [],
  config: { topic_template: DEFAULT_TOPIC, qos: 0, retain: false },
  payload_template: DEFAULT_PAYLOAD,
  max_attempts: 5,
  retry_backoff_sec: 5,
};

/** config 는 kind 마다 모양이 달라 Record<string, unknown> 이다. 표시할 때만 문자열로 좁힌다. */
const cfgText = (config: Record<string, unknown>, key: string, fallback = ""): string => {
  const v = config[key];
  return v === undefined || v === null || v === "" ? fallback : String(v);
};

const STATUS_LABEL: Record<string, string> = {
  pending: "대기",
  sent: "전송됨",
  failed: "실패 (재시도 예정)",
  expired: "포기 (시도 초과)",
};

const STATUS_CLASS: Record<string, string> = {
  pending: "text-muted",
  sent: "text-[#3f6b46]",
  failed: "text-warning",
  expired: "text-error",
};

export function OutboundAdmin() {
  const qc = useQueryClient();
  const { data: targets = [], isLoading } = useQuery({
    queryKey: ["outbound-targets"],
    queryFn: api.outboundTargets,
  });
  const { data: deliveries = [] } = useQuery({
    queryKey: ["outbound-deliveries"],
    queryFn: () => api.outboundDeliveries(),
    refetchInterval: 5000,
  });
  const [editing, setEditing] = useState<OutboundTarget | "new" | null>(null);

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteOutboundTarget(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["outbound-targets"] });
      qc.invalidateQueries({ queryKey: ["outbound-deliveries"] });
    },
  });

  const drain = useMutation({
    mutationFn: () => api.outboundDrain(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["outbound-deliveries"] }),
  });

  const pending = deliveries.filter((d) => d.status === "pending" || d.status === "failed").length;

  return (
    <>
      <Section
        title="아웃바운드 대상"
        desc="이벤트가 발생하면 여기 등록된 곳으로 내보냅니다."
        actions={
          <Button variant="primary" size="sm" onClick={() => setEditing("new")}>
            + 대상 추가
          </Button>
        }
      >
        <Card>
          <Table head={["사용", "이름", "종류", "토픽", "조건", "전송 / 실패", "최근 전송", ""]}>
            {isLoading ? (
              <EmptyRow colSpan={8} text="불러오는 중…" />
            ) : targets.length === 0 ? (
              <EmptyRow
                colSpan={8}
                text="등록된 대상이 없습니다. 이벤트를 상위 시스템이나 관제로 내보내려면 추가하세요."
              />
            ) : (
              targets.map((t) => (
                <tr key={t.id} className="transition-colors hover:bg-surface-soft">
                  <Td>
                    <Dot ok={t.enabled} />
                  </Td>
                  <Td className="font-medium text-body-strong">{t.name}</Td>
                  <Td className="text-[12.5px]">{t.kind === "mqtt" ? "MQTT 발행" : t.kind}</Td>
                  <Td className="max-w-[240px] truncate font-mono text-[12px]">
                    {cfgText(t.config, "topic_template", DEFAULT_TOPIC)}
                  </Td>
                  <Td className="text-[12px] text-muted">
                    {t.solution_codes.length === 0 && t.camera_ids.length === 0
                      ? "전체"
                      : [
                          t.solution_codes.length ? `항목 ${t.solution_codes.length}종` : null,
                          t.camera_ids.length ? `카메라 ${t.camera_ids.length}대` : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                  </Td>
                  <Td className="tnum whitespace-nowrap">
                    {t.sent_count}
                    {t.fail_count > 0 && (
                      <span className="ml-1 text-warning">/ {t.fail_count}</span>
                    )}
                  </Td>
                  <Td className="whitespace-nowrap text-muted">
                    {t.last_sent_at ? fmtAgo(t.last_sent_at) : "-"}
                  </Td>
                  <Td className="whitespace-nowrap text-right">
                    <Button size="sm" onClick={() => setEditing(t)}>
                      편집
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      className="ml-2"
                      onClick={() => remove.mutate(t.id)}
                    >
                      삭제
                    </Button>
                  </Td>
                </tr>
              ))
            )}
          </Table>

          {targets.some((t) => t.last_error) && (
            <p className="mt-4 rounded-lg border border-error/30 bg-error/5 px-4 py-3 text-[12.5px] text-error">
              최근 실패:{" "}
              {targets
                .filter((t) => t.last_error)
                .map((t) => `${t.name} — ${t.last_error}`)
                .join(" / ")}
            </p>
          )}

          <p className="mt-4 rounded-lg border border-warning/30 bg-warning/10 px-4 py-3 text-[12.5px] text-[#8a6708]">
            주의: 여기서 발행한 토픽에 <b className="font-semibold">인바운드 바인딩을 만들지 마세요.</b>{" "}
            서버는 모든 토픽을 구독하므로, 내보낸 것을 다시 받아 이벤트로 만들면 무한히 늘어납니다.
          </p>
        </Card>
      </Section>

      <Section
        title="전달 이력"
        desc="발송은 실패할 수 있습니다. 실패 사유와 재시도 상태를 여기서 확인합니다."
        actions={
          <>
            {pending > 0 && (
              <span className="text-[12.5px] text-warning">대기 {pending}건</span>
            )}
            <Button size="sm" disabled={drain.isPending} onClick={() => drain.mutate()}>
              지금 발송 시도
            </Button>
          </>
        }
      >
        <Card padded={false}>
          <div className="max-h-[420px] overflow-y-auto">
            <table className="w-full border-collapse text-[12.5px]">
              <thead className="sticky top-0 bg-canvas">
                <tr>
                  {["상태", "이벤트", "토픽", "시도", "시각", "사유"].map((h) => (
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
                {deliveries.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-10 text-center text-muted-soft">
                      전달 이력이 없습니다.
                    </td>
                  </tr>
                ) : (
                  deliveries.map((d) => (
                    <tr key={d.id}>
                      <td
                        className={cx(
                          "whitespace-nowrap border-b border-hairline/60 px-[14px] py-[9px] font-medium",
                          STATUS_CLASS[d.status] ?? "text-body",
                        )}
                      >
                        {STATUS_LABEL[d.status] ?? d.status}
                      </td>
                      <td className="tnum whitespace-nowrap border-b border-hairline/60 px-[14px] py-[9px]">
                        {d.event ?? "-"}
                      </td>
                      <td className="max-w-[260px] truncate border-b border-hairline/60 px-[14px] py-[9px] font-mono text-[12px] text-muted">
                        {d.topic}
                      </td>
                      <td className="tnum border-b border-hairline/60 px-[14px] py-[9px]">
                        {d.attempt}
                      </td>
                      <td className="tnum whitespace-nowrap border-b border-hairline/60 px-[14px] py-[9px] text-muted">
                        {d.sent_at
                          ? fmtDateTime(d.sent_at)
                          : d.next_attempt_at
                            ? `${fmtAgo(d.next_attempt_at)} 재시도`
                            : fmtDateTime(d.created_at)}
                      </td>
                      <td className="max-w-[280px] truncate border-b border-hairline/60 px-[14px] py-[9px] text-muted-soft">
                        {d.error || "-"}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </Section>

      {editing && (
        <TargetForm
          target={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  );
}

/* ═══════════════════════════════════════════════ 대상 편집 */

function TargetForm({ target, onClose }: { target: OutboundTarget | null; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: solutions = [] } = useSolutions();
  const { data: cameras = [] } = useCameras();
  const { data: fields = [] } = useQuery({
    queryKey: ["outbound-fields"],
    queryFn: api.outboundFields,
    staleTime: Infinity,
  });
  const [form, setForm] = useState<OutboundTargetInput>(EMPTY);
  const [error, setError] = useState("");
  const [test, setTest] = useState<OutboundTestResult | null>(null);

  useEffect(() => {
    if (target) {
      const { id: _id, last_sent_at: _l, sent_count: _s, fail_count: _f, last_error: _e, ...rest } =
        target;
      setForm(rest);
    } else {
      setForm(EMPTY);
    }
    setError("");
    setTest(null);
  }, [target?.id]);

  const set = <K extends keyof OutboundTargetInput>(key: K, value: OutboundTargetInput[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const setConfig = (key: string, value: unknown) =>
    setForm((f) => ({ ...f, config: { ...f.config, [key]: value } }));

  const save = useMutation({
    mutationFn: () =>
      target ? api.patchOutboundTarget(target.id, form) : api.createOutboundTarget(form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["outbound-targets"] });
      onClose();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "저장 중 오류가 발생했습니다."),
  });

  const probe = useMutation({
    mutationFn: () => api.testOutboundTarget(target!.id),
    onSuccess: setTest,
    onError: (err) =>
      setTest({
        ok: false,
        event: "",
        topic: "",
        payload: "",
        detail: err instanceof ApiError ? err.message : "시험 발송 실패",
      }),
  });

  const toggleIn = <T,>(list: T[], value: T): T[] =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  return (
    <Modal
      open
      onClose={onClose}
      title={target ? `대상 #${target.id} 편집` : "아웃바운드 대상 추가"}
      width={720}
      footer={
        <>
          <ErrorText>{error}</ErrorText>
          <div className="flex-1" />
          {target && (
            <Button disabled={probe.isPending} onClick={() => probe.mutate()}>
              {probe.isPending ? "발송 중…" : "시험 발송"}
            </Button>
          )}
          <Button onClick={onClose}>취소</Button>
          <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate()}>
            저장
          </Button>
        </>
      }
    >
      <div className="grid gap-5">
        <div className="grid gap-4 sm:grid-cols-[1.5fr_.8fr]">
          <Field label="이름">
            <Input
              value={form.name}
              onChange={(e) => set("name", e.target.value)}
              placeholder="예: 상위 관제 / 공장 알람"
              autoFocus
            />
          </Field>
          <Field label="종류">
            <Select value={form.kind} onChange={(e) => set("kind", e.target.value)}>
              <option value="mqtt">MQTT 발행</option>
            </Select>
          </Field>
        </div>

        <fieldset className="rounded-lg border border-hairline px-4 pb-4 pt-3">
          <legend className="px-2 text-[11px] font-semibold uppercase tracking-[.1em] text-muted-soft">
            어떤 이벤트를 보낼지
          </legend>
          <p className="mb-3 text-[12px] text-muted">아무것도 고르지 않으면 전부 보냅니다.</p>
          <div className="grid gap-3">
            <div>
              <span className="mb-[7px] block text-[11.5px] font-medium text-muted">탐지 항목</span>
              {solutions.length === 0 ? (
                <span className="text-[12px] text-muted-soft">등록된 항목이 없습니다.</span>
              ) : (
                <div className="flex flex-wrap gap-4">
                  {solutions.map((s) => (
                    <Checkbox
                      key={s.code}
                      checked={form.solution_codes.includes(s.code)}
                      label={`${s.short_name} (${s.code})`}
                      onChange={() => set("solution_codes", toggleIn(form.solution_codes, s.code))}
                    />
                  ))}
                </div>
              )}
            </div>
            <div>
              <span className="mb-[7px] block text-[11.5px] font-medium text-muted">카메라</span>
              {cameras.length === 0 ? (
                <span className="text-[12px] text-muted-soft">등록된 카메라가 없습니다.</span>
              ) : (
                <div className="flex flex-wrap gap-4">
                  {cameras.map((cam) => (
                    <Checkbox
                      key={cam.id}
                      checked={form.camera_ids.includes(cam.id)}
                      label={cam.location || cam.name}
                      onChange={() => set("camera_ids", toggleIn(form.camera_ids, cam.id))}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        </fieldset>

        <fieldset className="rounded-lg border border-hairline px-4 pb-4 pt-3">
          <legend className="px-2 text-[11px] font-semibold uppercase tracking-[.1em] text-muted-soft">
            어떤 모양으로 보낼지
          </legend>
          <Field label="토픽" hint="중괄호로 값을 채웁니다.">
            <Input
              value={cfgText(form.config, "topic_template")}
              onChange={(e) => setConfig("topic_template", e.target.value)}
              className="font-mono text-[12.5px]"
              placeholder={DEFAULT_TOPIC}
            />
          </Field>
          <Field label="페이로드" className="mt-4">
            <Textarea
              value={form.payload_template}
              onChange={(e) => set("payload_template", e.target.value)}
              className="min-h-[150px] font-mono text-[12px]"
            />
          </Field>
          <div className="mt-3 rounded-lg border border-hairline bg-surface-soft px-3 py-[10px]">
            <div className="mb-[6px] text-[11.5px] font-medium text-muted">쓸 수 있는 값</div>
            <div className="flex flex-wrap gap-[6px]">
              {fields.map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() =>
                    set("payload_template", `${form.payload_template}{${f}}`)
                  }
                  title="누르면 페이로드 끝에 넣습니다"
                  className="rounded-[5px] border border-hairline bg-canvas px-[7px] py-[2px] font-mono text-[11px] text-body transition-colors hover:border-primary/50 hover:text-primary-active"
                >
                  {`{${f}}`}
                </button>
              ))}
            </div>
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <Field label="QoS">
              <Select
                value={cfgText(form.config, "qos", "0")}
                onChange={(e) => setConfig("qos", Number(e.target.value))}
              >
                <option value="0">0 — 한 번만 보냄 (가장 빠름)</option>
                <option value="1">1 — 최소 한 번 도착 보장</option>
                <option value="2">2 — 정확히 한 번</option>
              </Select>
            </Field>
            <div className="self-end pb-2">
              <Checkbox
                checked={Boolean(form.config.retain)}
                onChange={(v) => setConfig("retain", v)}
                label="retain (구독자가 붙으면 마지막 값을 바로 받음)"
              />
            </div>
          </div>
        </fieldset>

        <fieldset className="rounded-lg border border-hairline px-4 pb-4 pt-3">
          <legend className="px-2 text-[11px] font-semibold uppercase tracking-[.1em] text-muted-soft">
            실패했을 때
          </legend>
          <p className="mb-3 text-[12px] leading-relaxed text-muted">
            수신 측이 죽어 있어도 이벤트를 잃지 않습니다. 발송 대기는 저장되어 서버를 재시작해도
            남고, 아래 횟수만큼 재시도한 뒤 포기합니다.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="최대 시도">
              <Input
                type="number"
                min={1}
                value={form.max_attempts}
                onChange={(e) => set("max_attempts", Math.max(1, Number(e.target.value) || 1))}
              />
            </Field>
            <Field label="재시도 간격 (초)" hint="실패할수록 두 배씩 늘어납니다(최대 5분).">
              <Input
                type="number"
                min={1}
                value={form.retry_backoff_sec}
                onChange={(e) => set("retry_backoff_sec", Math.max(1, Number(e.target.value) || 1))}
              />
            </Field>
          </div>
        </fieldset>

        <Checkbox checked={form.enabled} onChange={(v) => set("enabled", v)} label="사용" />

        {test && (
          <div
            className={cx(
              "rounded-lg border px-4 py-3 text-[12.5px]",
              test.ok
                ? "border-success/40 bg-success/10 text-[#3f6b46]"
                : "border-error/40 bg-error/10 text-error",
            )}
          >
            <div className="font-medium">{test.detail}</div>
            {test.topic && (
              <>
                <div className="mt-2 font-mono text-[11.5px]">토픽: {test.topic}</div>
                <pre className="mt-1 max-h-[160px] overflow-auto whitespace-pre-wrap break-all rounded border border-hairline bg-canvas p-2 font-mono text-[11px] text-body">
                  {test.payload}
                </pre>
              </>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

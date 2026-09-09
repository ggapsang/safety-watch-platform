/** 인바운드 바인딩 관리 — 플랫폼의 확장 지점.
 *
 * "어떤 토픽·페이로드를 어떤 탐지 항목의 이벤트로 볼지"를 여기서 정한다.
 * 코드 수정 없이 새 소스를 받아들일 수 있어야 한다는 요구가 이 화면으로 구현된다.
 *
 * 어떤 토픽도 특권을 갖지 않는다. 협력사가 정한 임의 토픽도, 우리가 만든 모듈이 쓰는 토픽도
 * 여기 등록해야 동작한다. '프리셋' 은 흔한 모양을 폼에 채워 주는 입력 도우미일 뿐이고
 * 만들어진 바인딩은 손으로 만든 것과 같은 데이터다.
 *
 * '시험' 이 이 화면의 핵심이다. MQTT 로그에서 본 메시지를 그대로 붙여 넣으면 어느 바인딩이
 * 걸리고 왜 안 걸리는지 바로 보여 준다. 이게 없으면 표현식을 손으로 맞춰 보며 추측해야 한다.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

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
import { fmtAgo } from "../lib/format";
import { useBindings, useCameras, useSolutions } from "../lib/hooks";
import type { Binding, BindingInput, BindingTestResult } from "../lib/types";

const EMPTY: BindingInput = {
  name: "",
  enabled: true,
  transport: "mqtt",
  payload_profile: "raw",
  priority: 100,
  live_only: false,
  topic_pattern: "",
  payload_filter: null,
  camera_from: "topic_mac",
  camera_expr: "",
  camera_id: null,
  item_from: "fixed",
  item_expr: "",
  solution_code: null,
  state_expr: "",
  state_active: "active",
  state_inactive: "inactive",
  module_expr: "",
  confidence_expr: "",
  ts_expr: "",
  boxes_expr: "",
  boxes_format: "xyxy_norm",
};

/** 프리셋 — 흔한 소스의 바인딩을 폼에 채워 준다.
 *
 * 서버는 프리셋을 모른다. 여기서 만들어진 바인딩은 손으로 만든 것과 완전히 같은 데이터이고
 * 지우면 없어진다. 어떤 토픽도 특권을 갖지 않는다는 원칙을 깨지 않으면서, 흔한 설정을
 * 매번 손으로 채우는 수고만 없앤다.
 */
const PRESETS: { id: string; label: string; hint: string; patch: Partial<BindingInput> }[] = [
  {
    id: "our-module",
    label: "우리 모듈 (탐지)",
    hint: "우리가 만든 모듈이 발행하는 모양. 카메라·항목을 페이로드에서 읽습니다.",
    patch: {
      name: "우리 모듈 탐지",
      topic_pattern: "aivision/detect/#",
      payload_profile: "raw",
      camera_from: "payload",
      camera_expr: "$.camera_id",
      item_from: "payload",
      item_expr: "$.item",
      state_expr: "$.state",
      state_active: "active",
      state_inactive: "inactive",
      module_expr: "$.module_id",
      confidence_expr: "$.confidence",
      ts_expr: "$.ts",
      boxes_expr: "$.boxes",
      boxes_format: "xyxy_norm",
      live_only: false,
    },
  },
  {
    id: "our-module-live",
    label: "우리 모듈 (라이브 박스)",
    hint: "영상 위 오버레이 전용. 이벤트로 쌓지 않고 화면으로만 흘립니다.",
    patch: {
      name: "우리 모듈 라이브 박스",
      topic_pattern: "aivision/live/+",
      payload_profile: "raw",
      camera_from: "topic_segment",
      camera_expr: "$topic[2]",
      item_from: "fixed",
      state_expr: "",
      boxes_expr: "$.boxes",
      boxes_format: "xyxy_norm",
      live_only: true,
    },
  },
  {
    id: "onvif-relay",
    label: "ONVIF 릴레이 (한화비전 등)",
    hint: "카메라가 ONVIF 양식을 MQTT 본문에 싣는 경우. 발생·해제가 따로 옵니다.",
    patch: {
      name: "ONVIF 릴레이",
      topic_pattern: "+/onvif-ej/#",
      payload_profile: "onvif",
      camera_from: "topic_mac",
      camera_expr: "",
      item_from: "fixed",
      state_expr: "$.Data.LogicalState",
      state_active: "active",
      state_inactive: "inactive",
      ts_expr: "$.UtcTime",
      boxes_expr: "",
      live_only: false,
    },
  },
  {
    id: "simple-alarm",
    label: "단순 알람 (페이로드 없음)",
    hint: "메시지가 오는 것 자체가 발생인 경우. 토픽 꼬리표만 고쳐 쓰세요.",
    patch: {
      name: "단순 알람",
      topic_pattern: "+/fireAlarm",
      payload_profile: "raw",
      camera_from: "topic_mac",
      camera_expr: "",
      item_from: "fixed",
      state_expr: "",
      confidence_expr: "",
      boxes_expr: "",
      live_only: false,
    },
  },
];

const CAMERA_FROM_LABEL: Record<BindingInput["camera_from"], string> = {
  topic_mac: "토픽의 MAC 주소",
  topic_segment: "토픽 조각",
  payload: "페이로드 값",
  fixed: "특정 카메라 고정",
};

const ITEM_FROM_LABEL: Record<BindingInput["item_from"], string> = {
  fixed: "항상 같은 항목",
  payload: "페이로드에서 읽음",
};

/** 표현식 문법 안내. 화면에서 설명할 수 있을 만큼 좁게 잡혀 있다. */
const EXPR_HINT = "$.a.b (페이로드) · $topic[2] (토픽 조각) · $mac · 그 외는 리터럴";

export function BindingAdmin() {
  const { state } = useLocation() as { state?: { topic?: string; payload?: string } };
  const qc = useQueryClient();
  const { data: bindings = [], isLoading } = useBindings();
  const { data: solutions = [] } = useSolutions();
  const { data: cameras = [] } = useCameras();
  const [editing, setEditing] = useState<Binding | "new" | null>(null);
  const [testOpen, setTestOpen] = useState(false);

  // MQTT 로그에서 '이 메시지로 바인딩 만들기' 로 넘어오면 시험 화면을 열어 준다.
  useEffect(() => {
    if (state?.topic) setTestOpen(true);
  }, [state?.topic]);

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteBinding(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bindings"] }),
  });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.patchBinding(id, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bindings"] }),
  });

  const camName = (id: number | null) =>
    id === null ? "-" : (cameras.find((c) => c.id === id)?.name ?? `#${id}`);

  return (
    <>
      <Section
        title="인바운드 바인딩"
        desc="들어온 메시지를 어떤 이벤트로 볼지 정합니다. 어떤 토픽이든 등록하면 동작합니다."
        actions={
          <>
            <Button size="sm" onClick={() => setTestOpen(true)}>
              시험
            </Button>
            <Button variant="primary" size="sm" onClick={() => setEditing("new")}>
              + 바인딩 추가
            </Button>
          </>
        }
      >
        <Card>
          <Table
            head={["사용", "이름", "토픽 패턴", "카메라", "탐지 항목", "상태 조건", "매칭", ""]}
          >
            {isLoading ? (
              <EmptyRow colSpan={8} text="불러오는 중…" />
            ) : bindings.length === 0 ? (
              <EmptyRow
                colSpan={8}
                text="등록된 바인딩이 없습니다. MQTT 로그에서 실제 메시지를 확인한 뒤 추가하세요."
              />
            ) : (
              bindings.map((b) => (
                <tr key={b.id} className="transition-colors hover:bg-surface-soft">
                  <Td>
                    <button
                      type="button"
                      onClick={() => toggle.mutate({ id: b.id, enabled: !b.enabled })}
                      title={b.enabled ? "끄기" : "켜기"}
                    >
                      <Dot ok={b.enabled} />
                    </button>
                  </Td>
                  <Td className="font-medium text-body-strong">
                    {b.name}
                    {b.live_only && (
                      <span className="ml-2 rounded-[4px] bg-primary-soft px-[6px] py-[1px] text-[10.5px] font-semibold text-primary-active">
                        라이브
                      </span>
                    )}
                  </Td>
                  <Td className="max-w-[280px] truncate font-mono text-[12px]">
                    {b.topic_pattern}
                    {b.payload_profile === "onvif" && (
                      <span className="ml-2 text-[10.5px] text-muted-soft">ONVIF</span>
                    )}
                  </Td>
                  <Td className="text-[12.5px]">
                    {b.camera_from === "fixed"
                      ? camName(b.camera_id)
                      : b.camera_from === "topic_mac"
                        ? "MAC"
                        : b.camera_expr}
                  </Td>
                  <Td className="text-[12.5px]">
                    {b.item_from === "fixed" ? b.solution_code : b.item_expr}
                  </Td>
                  <Td className="font-mono text-[12px] text-muted">
                    {b.state_expr || "(수신=발생)"}
                  </Td>
                  <Td className="tnum whitespace-nowrap text-muted">
                    {b.match_count}
                    {b.last_matched_at && (
                      <span className="ml-2 text-[11.5px] text-muted-soft">
                        {fmtAgo(b.last_matched_at)}
                      </span>
                    )}
                  </Td>
                  <Td className="whitespace-nowrap text-right">
                    <Button size="sm" onClick={() => setEditing(b)}>
                      편집
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      className="ml-2"
                      onClick={() => remove.mutate(b.id)}
                    >
                      삭제
                    </Button>
                  </Td>
                </tr>
              ))
            )}
          </Table>

          {solutions.length === 0 && (
            <p className="mt-4 rounded-lg border border-warning/30 bg-warning/10 px-4 py-3 text-[12.5px] text-[#8a6708]">
              등록된 탐지 항목이 없습니다. 이벤트를 만드는 바인딩은 "이 메시지 = 이 항목"을
              정하는 것이라 항목을 먼저 만들어야 합니다. 화면에 박스만 그리는
              <b className="font-semibold"> 라이브 전용</b> 바인딩은 항목 없이도 됩니다.
            </p>
          )}
        </Card>
      </Section>

      {editing && (
        <BindingForm
          binding={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
      {testOpen && (
        <TestPanel
          initialTopic={state?.topic ?? ""}
          initialPayload={state?.payload ?? ""}
          onClose={() => setTestOpen(false)}
        />
      )}
    </>
  );
}

/* ═══════════════════════════════════════════════ 바인딩 편집 */

function BindingForm({ binding, onClose }: { binding: Binding | null; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: solutions = [] } = useSolutions();
  const { data: cameras = [] } = useCameras();
  const [form, setForm] = useState<BindingInput>(EMPTY);
  const [error, setError] = useState("");
  const [advanced, setAdvanced] = useState(false);

  useEffect(() => {
    if (binding) {
      const { id: _id, last_matched_at: _l, match_count: _m, ...rest } = binding;
      setForm(rest);
      setAdvanced(
        !!(binding.confidence_expr ||
          binding.boxes_expr ||
          binding.ts_expr ||
          binding.module_expr),
      );
    } else {
      setForm({ ...EMPTY, solution_code: solutions[0]?.code ?? null });
      setAdvanced(false);
    }
    setError("");
  }, [binding?.id, solutions.length]);

  const set = <K extends keyof BindingInput>(key: K, value: BindingInput[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const save = useMutation({
    mutationFn: () =>
      binding ? api.patchBinding(binding.id, form) : api.createBinding(form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["bindings"] });
      onClose();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "저장 중 오류가 발생했습니다."),
  });

  return (
    <Modal
      open
      onClose={onClose}
      title={binding ? `바인딩 #${binding.id} 편집` : "바인딩 추가"}
      width={720}
      footer={
        <>
          <ErrorText>{error}</ErrorText>
          <div className="flex-1" />
          <Button onClick={onClose}>취소</Button>
          <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate()}>
            저장
          </Button>
        </>
      }
    >
      <div className="grid gap-5">
        {!binding && (
          <div className="rounded-lg border border-hairline bg-surface-soft px-4 py-3">
            <div className="mb-[3px] text-[11.5px] font-medium text-muted">프리셋</div>
            <p className="mb-[9px] text-[11px] text-muted-soft">
              흔한 모양을 아래 칸에 채워 줍니다. 채운 뒤 자유롭게 고칠 수 있고, 만들어진
              바인딩은 손으로 만든 것과 똑같습니다.
            </p>
            <div className="flex flex-wrap gap-2">
              {PRESETS.map((preset) => (
                <button
                  key={preset.id}
                  type="button"
                  title={preset.hint}
                  onClick={() =>
                    setForm((f) => ({
                      ...f,
                      ...preset.patch,
                      // 탐지 항목은 프리셋이 정할 수 없다. 현장에서 만든 것 중에 골라야 한다.
                      // 라이브 전용은 항목 자체가 필요 없으므로 비워 둔다.
                      solution_code: preset.patch.live_only
                        ? null
                        : (f.solution_code ?? solutions[0]?.code ?? null),
                    }))
                  }
                  className="rounded-full border border-hairline bg-canvas px-[13px] py-[5px] text-[12px] text-body transition-colors hover:border-primary/50 hover:text-primary-active"
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-[1.5fr_.7fr]">
          <Field label="이름" hint="이벤트의 '판정 주체'로 기록됩니다.">
            <Input
              value={form.name}
              onChange={(e) => set("name", e.target.value)}
              placeholder="예: 한화비전 화재 / 협력사 X"
              autoFocus
            />
          </Field>
          <Field label="우선순위" hint="작을수록 먼저">
            <Input
              type="number"
              value={form.priority}
              onChange={(e) => set("priority", Number(e.target.value) || 100)}
            />
          </Field>
        </div>

        <fieldset className="rounded-lg border border-hairline px-4 pb-4 pt-3">
          <legend className="px-2 text-[11px] font-semibold uppercase tracking-[.1em] text-muted-soft">
            무엇이 걸리는가
          </legend>
          <Field
            label="토픽 패턴"
            hint="MQTT 와일드카드. + 는 한 조각, # 는 나머지 전부. 예: +/fireAlarm"
          >
            <Input
              value={form.topic_pattern}
              onChange={(e) => set("topic_pattern", e.target.value)}
              className="font-mono text-[12.5px]"
              placeholder="+/fireAlarm"
            />
          </Field>
          <Field
            label="페이로드 형식"
            className="mt-4"
            hint="ONVIF 를 고르면 SimpleItem 배열을 Data.State 같은 평면 키로 펴 줍니다."
          >
            <Select
              value={form.payload_profile}
              onChange={(e) => set("payload_profile", e.target.value as "raw" | "onvif")}
            >
              <option value="raw">일반 JSON</option>
              <option value="onvif">ONVIF (SimpleItem 평탄화)</option>
            </Select>
          </Field>
        </fieldset>

        <fieldset className="rounded-lg border border-hairline px-4 pb-4 pt-3">
          <legend className="px-2 text-[11px] font-semibold uppercase tracking-[.1em] text-muted-soft">
            어떤 카메라인가
          </legend>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="식별 방법">
              <Select
                value={form.camera_from}
                onChange={(e) =>
                  set("camera_from", e.target.value as BindingInput["camera_from"])
                }
              >
                {Object.entries(CAMERA_FROM_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </Select>
            </Field>
            {form.camera_from === "fixed" ? (
              <Field label="카메라">
                <Select
                  value={form.camera_id ?? ""}
                  onChange={(e) =>
                    set("camera_id", e.target.value ? Number(e.target.value) : null)
                  }
                >
                  <option value="">선택하세요</option>
                  {cameras.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} · {c.location}
                    </option>
                  ))}
                </Select>
              </Field>
            ) : form.camera_from === "topic_mac" ? (
              <div className="self-end pb-2 text-[12px] text-muted">
                토픽 앞머리의 MAC 으로 등록된 카메라를 찾습니다.
              </div>
            ) : (
              <Field label="표현식" hint={EXPR_HINT}>
                <Input
                  value={form.camera_expr}
                  onChange={(e) => set("camera_expr", e.target.value)}
                  className="font-mono text-[12.5px]"
                  placeholder={form.camera_from === "payload" ? "$.camera_id" : "$topic[1]"}
                />
              </Field>
            )}
          </div>
        </fieldset>

        <fieldset className="rounded-lg border border-hairline px-4 pb-4 pt-3">
          <legend className="px-2 text-[11px] font-semibold uppercase tracking-[.1em] text-muted-soft">
            어떤 탐지 항목인가
          </legend>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="결정 방법">
              <Select
                value={form.item_from}
                onChange={(e) => set("item_from", e.target.value as BindingInput["item_from"])}
              >
                {Object.entries(ITEM_FROM_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </Select>
            </Field>
            {form.item_from === "fixed" ? (
              <Field
                label="탐지 항목"
                hint={
                  form.live_only
                    ? "라이브 전용은 비워 두어도 됩니다. 항목은 '무엇을 이벤트로 볼지' 를 정하는 것인데, 라이브 박스는 이벤트가 아닙니다."
                    : undefined
                }
              >
                <Select
                  value={form.solution_code ?? ""}
                  onChange={(e) => set("solution_code", e.target.value || null)}
                >
                  <option value="">{form.live_only ? "없음 (박스만 그림)" : "선택하세요"}</option>
                  {solutions.map((s) => (
                    <option key={s.code} value={s.code}>
                      {s.short_name} ({s.code})
                    </option>
                  ))}
                </Select>
              </Field>
            ) : (
              <Field label="표현식" hint="결과가 탐지 항목 코드와 일치해야 합니다.">
                <Input
                  value={form.item_expr}
                  onChange={(e) => set("item_expr", e.target.value)}
                  className="font-mono text-[12.5px]"
                  placeholder="$.item"
                />
              </Field>
            )}
          </div>
        </fieldset>

        <fieldset className="rounded-lg border border-hairline px-4 pb-4 pt-3">
          <legend className="px-2 text-[11px] font-semibold uppercase tracking-[.1em] text-muted-soft">
            발생 · 해제 판정
          </legend>
          <Field
            label="상태 표현식"
            hint="비우면 '메시지 수신 자체가 발생'입니다. 해제 신호가 따로 오는 장비면 채우세요."
          >
            <Input
              value={form.state_expr}
              onChange={(e) => set("state_expr", e.target.value)}
              className="font-mono text-[12.5px]"
              placeholder="$.Data.LogicalState"
            />
          </Field>
          {form.state_expr && (
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <Field label="발생 값">
                <Input
                  value={form.state_active}
                  onChange={(e) => set("state_active", e.target.value)}
                  className="font-mono text-[12.5px]"
                />
              </Field>
              <Field label="해제 값">
                <Input
                  value={form.state_inactive}
                  onChange={(e) => set("state_inactive", e.target.value)}
                  className="font-mono text-[12.5px]"
                />
              </Field>
            </div>
          )}
        </fieldset>

        <div>
          <button
            type="button"
            onClick={() => setAdvanced((v) => !v)}
            className="text-[12.5px] font-medium text-primary hover:text-primary-active"
          >
            {advanced ? "부가 정보 접기" : "부가 정보 (판정 주체 · 신뢰도 · 시각 · 바운딩 박스)"}
          </button>
          {advanced && (
            <div className="mt-3 grid gap-4 rounded-lg border border-hairline bg-surface-soft p-4">
              <Field
                label="판정 주체 표현식"
                hint="무엇이 판정했는지 페이로드에 실려 오면 뽑습니다. 비워도 됩니다."
              >
                <Input
                  value={form.module_expr}
                  onChange={(e) => set("module_expr", e.target.value)}
                  className="font-mono text-[12.5px]"
                  placeholder="$.module_id"
                />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="신뢰도 표현식" hint="0~100 으로 오면 자동으로 0~1 로 바꿉니다.">
                  <Input
                    value={form.confidence_expr}
                    onChange={(e) => set("confidence_expr", e.target.value)}
                    className="font-mono text-[12.5px]"
                    placeholder="$.conf"
                  />
                </Field>
                <Field label="발생시각 표현식" hint="비우면 수신 시각을 씁니다.">
                  <Input
                    value={form.ts_expr}
                    onChange={(e) => set("ts_expr", e.target.value)}
                    className="font-mono text-[12.5px]"
                    placeholder="$.UtcTime"
                  />
                </Field>
              </div>
              <div className="grid gap-4 sm:grid-cols-[1.4fr_1fr]">
                <Field label="박스 배열 표현식">
                  <Input
                    value={form.boxes_expr}
                    onChange={(e) => set("boxes_expr", e.target.value)}
                    className="font-mono text-[12.5px]"
                    placeholder="$.boxes"
                  />
                </Field>
                <Field label="박스 좌표 형식" hint="어떤 형식이든 0~1 정규화로 바꿔 저장합니다.">
                  <Select
                    value={form.boxes_format}
                    onChange={(e) =>
                      set("boxes_format", e.target.value as BindingInput["boxes_format"])
                    }
                  >
                    <option value="xyxy_norm">x1y1x2y2 (0~1)</option>
                    <option value="xyxy_px">x1y1x2y2 (픽셀)</option>
                    <option value="xywh_px">xywh (픽셀)</option>
                    <option value="cxcywh_norm">중심xywh (0~1)</option>
                  </Select>
                </Field>
              </div>
              <Checkbox
                checked={form.live_only}
                onChange={(v) => set("live_only", v)}
                label="라이브 전용 (이벤트로 쌓지 않고 화면 오버레이로만 흘림)"
              />
            </div>
          )}
        </div>

        <Checkbox checked={form.enabled} onChange={(v) => set("enabled", v)} label="사용" />
      </div>
    </Modal>
  );
}

/* ═══════════════════════════════════════════════ 시험 */

function TestPanel({
  initialTopic,
  initialPayload,
  onClose,
}: {
  initialTopic: string;
  initialPayload: string;
  onClose: () => void;
}) {
  const [topic, setTopic] = useState(initialTopic);
  const [payload, setPayload] = useState(initialPayload);
  const [results, setResults] = useState<BindingTestResult[] | null>(null);

  const run = useMutation({
    mutationFn: () => api.testBinding(topic, payload),
    onSuccess: setResults,
  });

  // 넘어온 메시지가 있으면 바로 한 번 돌려 준다.
  useEffect(() => {
    if (initialTopic) run.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const matched = results?.filter((r) => r.matched) ?? [];

  return (
    <Modal
      open
      onClose={onClose}
      title="바인딩 시험"
      width={760}
      footer={
        <>
          <div className="flex-1" />
          <Button onClick={onClose}>닫기</Button>
          <Button variant="primary" disabled={run.isPending} onClick={() => run.mutate()}>
            시험 실행
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        <p className="text-[12.5px] leading-relaxed text-muted">
          실제 메시지를 붙여 넣으면 어느 바인딩이 걸리는지, 안 걸리면 왜인지 보여 줍니다.
          <b className="font-semibold text-body"> MQTT 로그</b> 화면에서 메시지를 눌러 그대로
          가져올 수 있습니다.
        </p>
        <Field label="토픽">
          <Input
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            className="font-mono text-[12.5px]"
            placeholder="E4:30:22:F3:31:AA/fireAlarm"
          />
        </Field>
        <Field label="페이로드" hint="JSON 또는 비워 둠">
          <Textarea
            value={payload}
            onChange={(e) => setPayload(e.target.value)}
            className="font-mono text-[12px]"
            placeholder='{"Data":{"LogicalState":"active"}}'
          />
        </Field>

        {results && (
          <div className="rounded-lg border border-hairline">
            <div className="border-b border-hairline px-4 py-[10px] text-[12.5px]">
              {matched.length > 0 ? (
                <span className="font-semibold text-[#3f6b46]">
                  {matched.length}개 바인딩이 걸립니다 — 이벤트가 만들어집니다
                </span>
              ) : (
                <span className="font-semibold text-error">
                  걸리는 바인딩이 없습니다 — 이 메시지는 무시됩니다
                </span>
              )}
            </div>
            <div className="max-h-[320px] overflow-y-auto">
              {results.length === 0 ? (
                <p className="px-4 py-6 text-center text-[12.5px] text-muted-soft">
                  등록된 바인딩이 없습니다.
                </p>
              ) : (
                results.map((r) => (
                  <div
                    key={r.binding_id}
                    className={cx(
                      "flex items-start gap-3 border-b border-hairline/60 px-4 py-[10px] text-[12.5px] last:border-b-0",
                      r.matched && "bg-success/5",
                    )}
                  >
                    <Dot ok={r.matched} className="mt-[6px]" />
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-body-strong">{r.binding_name}</div>
                      {r.matched ? (
                        <div className="mt-[3px] text-muted">
                          카메라 #{r.camera_id} · 항목 {r.item} · 상태 {r.state}
                          {r.boxes > 0 && ` · 박스 ${r.boxes}개`}
                        </div>
                      ) : (
                        <div className="mt-[3px] text-muted-soft">{r.reason}</div>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

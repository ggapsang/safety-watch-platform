/** 관리자 — 카메라 등록, 운영 설정, 시스템 상태.
 *
 * 이 화면의 목적은 '현장에서 배포 없이 바꿀 수 있게' 하는 것이다.
 *
 * 카메라 등록에 필요한 것은 IP 와 계정뿐이다. MQTT 토픽 매핑이나 탐지 항목처럼
 * 아직 정해지지 않은 것을 등록 단계에서 묻지 않는다. 무엇을 탐지할지 정해지면
 * 그때 항목을 추가하고 카메라에 연결한다.
 *
 * 주의: 로그인이 없다. 폐쇄망 전제다. 외부 접근이 가능한 망에 올릴 때는 이 화면부터 막아야 한다.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

import {
  Button,
  Card,
  CardTitle,
  Checkbox,
  Dot,
  EmptyRow,
  ErrorText,
  Field,
  Input,
  Modal,
  Section,
  Table,
  Tabs,
  Td,
  cx,
} from "../components/ui";
import { api, ApiError } from "../lib/api";
import { fmtAgo } from "../lib/format";
import { useCameras, useSettings, useSystem } from "../lib/hooks";
import { BindingAdmin } from "./BindingAdmin";
import type { Camera, CameraInput, CameraTestResult } from "../lib/types";

type Tab = "cameras" | "bindings" | "settings" | "system";

const TABS: { id: Tab; label: string }[] = [
  { id: "cameras", label: "카메라 등록" },
  { id: "bindings", label: "인바운드 바인딩" },
  { id: "settings", label: "운영 설정" },
  { id: "system", label: "시스템 상태" },
];

const EMPTY: CameraInput = {
  name: "",
  location: "",
  ip: "",
  rtsp_port: 554,
  rtsp_path: "/profile2/media.smp",
  username: "",
  password: "",
  note: "",
  enabled: true,
  record_enabled: false,
  record_retention_days: 3,
};

export function Admin() {
  const { state } = useLocation() as { state?: { tab?: Tab } };
  const [tab, setTab] = useState<Tab>(state?.tab ?? "cameras");

  useEffect(() => {
    if (state?.tab) setTab(state.tab);
  }, [state?.tab]);
  return (
    <>
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === "cameras" && <CameraAdmin />}
      {tab === "bindings" && <BindingAdmin />}
      {tab === "settings" && <SettingsAdmin />}
      {tab === "system" && <SystemAdmin />}
    </>
  );
}

/* ═══════════════════════════════════════════════════ 카메라 */

function CameraAdmin() {
  const qc = useQueryClient();
  const { data: cameras = [], isLoading } = useCameras();
  const [editing, setEditing] = useState<Camera | "new" | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Camera | null>(null);

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteCamera(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cameras"] });
      setConfirmDelete(null);
    },
  });

  return (
    <>
      <Section
        title="등록 카메라"
        desc="IP 를 등록하면 즉시 영상 수신을 시작합니다."
        actions={
          <Button variant="primary" size="sm" onClick={() => setEditing("new")}>
            + 카메라 등록
          </Button>
        }
      >
        <Card>
          <Table head={["상태", "이름", "설치 위치", "IP · 경로", "녹화", "최근 수신", ""]}>
            {isLoading ? (
              <EmptyRow colSpan={7} text="불러오는 중…" />
            ) : cameras.length === 0 ? (
              <EmptyRow colSpan={7} text="등록된 카메라가 없습니다. 우측 상단에서 등록하세요." />
            ) : (
              cameras.map((c) => (
                <tr key={c.id} className="transition-colors hover:bg-surface-soft">
                  <Td>
                    <span className="inline-flex items-center gap-2 whitespace-nowrap">
                      <Dot ok={c.status === "normal"} />
                      {c.enabled ? (c.status === "normal" ? "정상" : "오프라인") : "사용 안 함"}
                    </span>
                  </Td>
                  <Td className="font-medium text-body-strong">{c.name}</Td>
                  <Td>{c.location}</Td>
                  <Td className="tnum whitespace-nowrap">
                    {c.ip}
                    {c.rtsp_port !== 554 && `:${c.rtsp_port}`}
                    <span className="ml-1 text-muted-soft">{c.rtsp_path}</span>
                  </Td>
                  <Td className="whitespace-nowrap text-muted">
                    {c.record_enabled ? `${c.record_retention_days}일 보존` : "안 함"}
                  </Td>
                  <Td className="whitespace-nowrap text-muted">
                    {c.last_seen_at ? fmtAgo(c.last_seen_at) : "-"}
                  </Td>
                  <Td className="whitespace-nowrap text-right">
                    <Button size="sm" onClick={() => setEditing(c)}>
                      편집
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      className="ml-2"
                      onClick={() => setConfirmDelete(c)}
                    >
                      삭제
                    </Button>
                  </Td>
                </tr>
              ))
            )}
          </Table>
        </Card>
      </Section>

      {editing && (
        <CameraForm camera={editing === "new" ? null : editing} onClose={() => setEditing(null)} />
      )}

      <Modal
        open={!!confirmDelete}
        onClose={() => setConfirmDelete(null)}
        title="카메라 삭제"
        width={460}
        footer={
          <>
            <div className="flex-1" />
            <Button onClick={() => setConfirmDelete(null)}>취소</Button>
            <Button
              variant="danger"
              disabled={remove.isPending}
              onClick={() => confirmDelete && remove.mutate(confirmDelete.id)}
            >
              삭제
            </Button>
          </>
        }
      >
        <p className="text-[13.5px] leading-relaxed text-body">
          <b className="font-semibold text-ink">{confirmDelete?.name}</b> 을(를) 삭제하면
          <b className="font-semibold text-error"> 이 카메라의 이벤트 이력도 함께 삭제됩니다.</b>
          <br />
          이력을 남기려면 삭제 대신 편집에서 <b className="font-semibold text-ink">사용 안 함</b>으로
          바꾸세요.
        </p>
      </Modal>
    </>
  );
}

function CameraForm({ camera, onClose }: { camera: Camera | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<CameraInput>(EMPTY);
  const [error, setError] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [test, setTest] = useState<CameraTestResult | null>(null);

  useEffect(() => {
    setForm(
      camera
        ? {
            name: camera.name,
            location: camera.location,
            ip: camera.ip,
            rtsp_port: camera.rtsp_port,
            rtsp_path: camera.rtsp_path,
            username: camera.username,
            password: "",
            note: camera.note,
            enabled: camera.enabled,
            record_enabled: camera.record_enabled,
            record_retention_days: camera.record_retention_days,
          }
        : EMPTY,
    );
    setError("");
    setTest(null);
    setAdvanced(false);
  }, [camera?.id]);

  const set = <K extends keyof CameraInput>(key: K, value: CameraInput[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const save = useMutation({
    mutationFn: () => (camera ? api.patchCamera(camera.id, form) : api.createCamera(form)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cameras"] });
      onClose();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "저장 중 오류가 발생했습니다."),
  });

  const probe = useMutation({
    mutationFn: () => (camera ? api.testCamera(camera.id) : api.testEndpoint(form)),
    onSuccess: setTest,
    onError: (err) =>
      setTest({
        ok: false,
        rtsp_ok: false,
        detail: err instanceof ApiError ? err.message : "연결 테스트 실패",
        width: 0,
        height: 0,
        elapsed_ms: 0,
      }),
  });

  const submit = () => {
    if (!form.ip.trim()) {
      setError("IP 주소를 입력하세요.");
      return;
    }
    setError("");
    // 이름·설치 위치를 비워 두면 IP 로 채운다. 목록에서 무엇인지 알아볼 수만 있으면 된다.
    save.mutate();
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={camera ? `카메라 #${camera.id} 편집` : "카메라 등록"}
      width={620}
      footer={
        <>
          <ErrorText>{error}</ErrorText>
          <div className="flex-1" />
          <Button disabled={probe.isPending} onClick={() => probe.mutate()}>
            {probe.isPending ? "확인 중…" : "연결 테스트"}
          </Button>
          <Button onClick={onClose}>취소</Button>
          <Button variant="primary" disabled={save.isPending} onClick={submit}>
            저장
          </Button>
        </>
      }
    >
      <div className="grid gap-5">
        <Field label="IP 주소" hint="카메라의 네트워크 주소입니다.">
          <Input
            value={form.ip}
            onChange={(e) => set("ip", e.target.value)}
            placeholder="192.168.10.11"
            autoFocus
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="계정">
            <Input
              value={form.username}
              onChange={(e) => set("username", e.target.value)}
              placeholder="admin"
            />
          </Field>
          <Field
            label="비밀번호"
            hint={
              camera?.has_password ? "비워 두면 기존 비밀번호를 유지합니다." : "암호화해 저장합니다."
            }
          >
            <Input
              type="password"
              value={form.password}
              onChange={(e) => set("password", e.target.value)}
              placeholder={camera?.has_password ? "********" : ""}
            />
          </Field>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="이름" hint="비워 두면 IP 로 채웁니다.">
            <Input
              value={form.name}
              onChange={(e) => set("name", e.target.value)}
              placeholder="현장 카메라"
            />
          </Field>
          <Field label="설치 위치" hint="목록·이벤트에서 이 이름으로 표시됩니다.">
            <Input
              value={form.location}
              onChange={(e) => set("location", e.target.value)}
              placeholder="예: 1번 게이트"
            />
          </Field>
        </div>

        <div>
          <button
            type="button"
            onClick={() => setAdvanced((v) => !v)}
            className="text-[12.5px] font-medium text-primary hover:text-primary-active"
          >
            {advanced ? "고급 설정 접기" : "고급 설정 (포트 · 스트림 경로)"}
          </button>
          {advanced && (
            <div className="mt-3 grid gap-4 rounded-lg border border-hairline bg-surface-soft p-4 sm:grid-cols-[.5fr_1.5fr]">
              <Field label="RTSP 포트">
                <Input
                  type="number"
                  value={form.rtsp_port}
                  onChange={(e) => set("rtsp_port", Number(e.target.value) || 554)}
                />
              </Field>
              <Field
                label="스트림 경로"
                hint="한화비전 기본값은 /profile2/media.smp 입니다. 채널·프로파일에 따라 다를 수 있습니다."
              >
                <Input value={form.rtsp_path} onChange={(e) => set("rtsp_path", e.target.value)} />
              </Field>
              <Field label="비고" className="sm:col-span-2">
                <Input value={form.note} onChange={(e) => set("note", e.target.value)} />
              </Field>
            </div>
          )}
        </div>

        <fieldset className="rounded-lg border border-hairline px-4 pb-4 pt-3">
          <legend className="px-2 text-[11px] font-semibold uppercase tracking-[.1em] text-muted-soft">
            녹화
          </legend>
          <Checkbox
            checked={form.record_enabled}
            onChange={(v) => set("record_enabled", v)}
            label="상시 녹화"
          />
          {form.record_enabled && (
            <Field
              label="보존 기간 (일)"
              className="mt-4 max-w-[220px]"
              hint="이 기간이 지난 상시 녹화는 자동 삭제됩니다. 이벤트 클립은 따로 보관되어 남습니다."
            >
              <Input
                type="number"
                min={1}
                value={form.record_retention_days}
                onChange={(e) =>
                  set("record_retention_days", Math.max(1, Number(e.target.value) || 1))
                }
              />
            </Field>
          )}
        </fieldset>

        <Checkbox
          checked={form.enabled}
          onChange={(v) => set("enabled", v)}
          label="사용 (끄면 영상 수신을 멈춥니다)"
        />

        {test && (
          <div
            className={cx(
              "rounded-lg border px-4 py-3 text-[12.5px]",
              test.ok
                ? "border-success/40 bg-success/10 text-[#3f6b46]"
                : "border-error/40 bg-error/10 text-error",
            )}
          >
            {test.detail}
            {test.elapsed_ms > 0 && (
              <span className="tnum ml-2 opacity-70">({Math.round(test.elapsed_ms)}ms)</span>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}

/* ═══════════════════════════════════════════════════ 운영 설정 */

function SettingsAdmin() {
  const qc = useQueryClient();
  const { data: settings } = useSettings();
  const [retention, setRetention] = useState(7);
  const [dedup, setDedup] = useState(20);
  const [snapshot, setSnapshot] = useState(true);
  const [saved, setSaved] = useState("");

  useEffect(() => {
    if (!settings) return;
    setRetention(settings.mqtt_log_retention_days);
    setDedup(settings.event_dedup_sec);
    setSnapshot(settings.snapshot_on_event);
  }, [settings]);

  const save = useMutation({
    mutationFn: () =>
      api.saveSettings({
        mqtt_log_retention_days: retention,
        event_dedup_sec: dedup,
        snapshot_on_event: snapshot,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      setSaved("저장했습니다.");
      window.setTimeout(() => setSaved(""), 2500);
    },
  });

  const purge = useMutation({
    mutationFn: () => api.purgeMqttLog(),
    onSuccess: (r) => {
      setSaved(`MQTT 원문 로그 ${r.deleted}건을 정리했습니다.`);
      window.setTimeout(() => setSaved(""), 3000);
    },
  });

  return (
    <Section
      title="운영 설정"
      desc="이벤트 처리와 로그 보관 방식을 조정합니다. 저장 즉시 반영됩니다."
      actions={
        <>
          <span className="text-[12.5px] text-success">{saved}</span>
          <Button variant="primary" size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
            저장
          </Button>
        </>
      }
    >
      <div className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardTitle title="이벤트 · 로그" desc="적재량과 중복 처리에 영향을 줍니다." />
          <div className="grid gap-4">
            <Field
              label="이벤트 중복 억제 (초)"
              hint="같은 카메라·같은 항목의 연속 신호를 이 시간 안에서는 한 건으로 묶습니다."
            >
              <Input
                type="number"
                min={0}
                value={dedup}
                onChange={(e) => setDedup(Number(e.target.value) || 0)}
              />
            </Field>
            <Field
              label="MQTT 원문 로그 보존 (일)"
              hint="지난 메시지를 되짚어 볼 수 있는 기간입니다."
            >
              <Input
                type="number"
                min={1}
                value={retention}
                onChange={(e) => setRetention(Number(e.target.value) || 1)}
              />
            </Field>
            <Checkbox
              checked={snapshot}
              onChange={setSnapshot}
              label="이벤트 발생 시 화면 캡쳐 저장"
            />
            <div>
              <Button size="sm" disabled={purge.isPending} onClick={() => purge.mutate()}>
                보존기간 지난 MQTT 로그 지금 정리
              </Button>
            </div>
          </div>
        </Card>

        <Card>
          <CardTitle title="탐지 항목" desc="무엇을 이벤트로 볼지 정하는 곳입니다." />
          <p className="text-[13px] leading-relaxed text-muted">
            아직 등록된 탐지 항목이 없습니다. 지금 서버는 카메라가 보내는 MQTT 메시지를 원문 그대로
            받아 보관만 합니다.
            <br />
            <br />
            <b className="font-semibold text-body">MQTT 로그</b> 화면에서 실제로 어떤 토픽·페이로드가
            들어오는지 확인한 뒤, 무엇을 이벤트로 볼지 정해지면{" "}
            <b className="font-semibold text-body">인바운드 바인딩</b> 탭에서 규칙을 만듭니다.
            우리 토픽 규약이 아니어도 됩니다.
          </p>
        </Card>
      </div>
    </Section>
  );
}

/* ═══════════════════════════════════════════════════ 시스템 */

function SystemAdmin() {
  const { data: sys } = useSystem();
  const { data: cameras = [] } = useCameras();

  return (
    <>
      <Section title="수신 상태" desc="MQTT 메시지가 들어오고 있는지 보여 줍니다.">
        <Card>
          <Table head={["소스", "상태", "브로커 연결", "수신 / 매칭", "카메라 / 규칙", "메시지"]}>
            {(sys?.detection_sources.length ?? 0) === 0 ? (
              <EmptyRow colSpan={6} text="수신 소스가 없습니다." />
            ) : (
              sys!.detection_sources.map((s) => (
                <tr key={s.name}>
                  <Td className="font-medium text-body-strong">{s.name}</Td>
                  <Td>
                    <span className="inline-flex items-center gap-2">
                      <Dot ok={s.running} />
                      {s.running ? "동작 중" : "정지"}
                    </span>
                  </Td>
                  <Td>{s.connected === undefined ? "-" : s.connected ? "연결됨" : "끊김"}</Td>
                  <Td className="tnum">
                    {s.received ?? 0} / {s.matched ?? 0}
                  </Td>
                  <Td className="tnum">
                    {s.cameras ?? 0} / {s.rules ?? 0}
                  </Td>
                  <Td className="text-muted">{s.last_error || "-"}</Td>
                </tr>
              ))
            )}
          </Table>
          {sys?.mqtt && (
            <p className="mt-4 text-[12px] text-muted">
              브로커 {sys.mqtt.host}:{sys.mqtt.port} · 구독{" "}
              <span className="font-mono">{sys.mqtt.subscribe}</span> · 최근 1시간 수신{" "}
              <span className="tnum">{sys.mqtt.messages_1h}</span>건 · 브라우저 직결 주소{" "}
              <span className="font-mono">{sys.mqtt.ws_url}</span>
            </p>
          )}
        </Card>
      </Section>

      <Section title="영상 스트림" desc="카메라별 RTSP 수신 상태입니다.">
        <Card>
          <Table head={["카메라", "연결", "해상도", "수신 FPS", "마지막 프레임", "재연결", "오류"]}>
            {Object.keys(sys?.streams ?? {}).length === 0 ? (
              <EmptyRow colSpan={7} text="동작 중인 스트림이 없습니다." />
            ) : (
              Object.values(sys!.streams).map((st) => {
                const cam = cameras.find((c) => c.id === st.camera_id);
                return (
                  <tr key={st.camera_id}>
                    <Td className="font-medium text-body-strong">
                      {cam ? `${cam.name} · ${cam.location}` : `#${st.camera_id}`}
                    </Td>
                    <Td>
                      <span className="inline-flex items-center gap-2">
                        <Dot ok={st.connected} />
                        {st.connected ? "정상" : "끊김"}
                      </span>
                    </Td>
                    <Td className="tnum">{st.width ? `${st.width}x${st.height}` : "-"}</Td>
                    <Td className="tnum">{st.fps}</Td>
                    <Td className="tnum">
                      {Number.isFinite(st.stale_sec) ? `${st.stale_sec}초 전` : "-"}
                    </Td>
                    <Td className="tnum">{st.reconnects}</Td>
                    <Td className="text-muted">{st.last_error || "-"}</Td>
                  </tr>
                );
              })
            )}
          </Table>
          {sys?.stream && (
            <p className="mt-4 text-[12px] text-muted">
              송출 설정 · 최대 {sys.stream.fps}fps · 폭 {sys.stream.max_width}px · JPEG 품질{" "}
              {sys.stream.jpeg_quality}
            </p>
          )}
        </Card>
      </Section>
    </>
  );
}

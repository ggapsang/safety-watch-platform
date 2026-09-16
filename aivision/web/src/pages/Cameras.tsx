/** 카메라 현황 — 상태 확인과 '이름 · 설치 위치' 인라인 수정.
 *
 * 시안에 있던 '설치 위치를 표에서 바로 고치는' 흐름을 유지했다. 현장에서 카메라를 옮기면
 * 관리자 화면까지 들어가지 않고 여기서 고치는 편이 자연스럽다.
 * (접속 정보·솔루션 매핑 같은 구조적 변경은 관리자 화면에서 한다.)
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

import { CameraStrip } from "../components/CameraStrip";
import { LiveVideo } from "../components/LiveVideo";
import {
  Button,
  Card,
  Dot,
  EmptyRow,
  ErrorText,
  Field,
  Input,
  KpiCard,
  Modal,
  Section,
  Table,
  Tabs,
  Td,
  cx,
} from "../components/ui";
import { api, ApiError } from "../lib/api";
import { fmtAgo, fmtHours } from "../lib/format";
import { useCameras, useClock, useLiveBoxes } from "../lib/hooks";
import type { Camera } from "../lib/types";

type Filter = "all" | "normal" | "offline";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "전체" },
  { id: "normal", label: "정상" },
  { id: "offline", label: "이상" },
];

export function Cameras() {
  const { state } = useLocation() as { state?: { filter?: Filter } };
  const now = useClock();
  const { data: cameras = [], isLoading } = useCameras();
  const liveBoxes = useLiveBoxes();
  const [filter, setFilter] = useState<Filter>(state?.filter ?? "all");
  const [editing, setEditing] = useState<Camera | null>(null);
  const [focused, setFocused] = useState<number | null>(null);

  useEffect(() => {
    if (state?.filter) setFilter(state.filter);
  }, [state?.filter]);

  // 고른 카메라. 아직 고르지 않았거나 그 카메라가 사라졌으면 첫 번째를 보여 준다.
  const focusedCam = cameras.find((c) => c.id === focused) ?? cameras[0] ?? null;

  const shown = cameras.filter((c) =>
    filter === "all" ? true : filter === "normal" ? c.status === "normal" : c.status !== "normal",
  );
  const normal = cameras.filter((c) => c.status === "normal").length;

  return (
    <>
      <div className="mb-7 grid grid-cols-2 gap-4 xl:grid-cols-4">
        <KpiCard label="전체 카메라" value={cameras.length} sub="물리 채널" />
        <KpiCard label="정상" value={normal} sub="정상 가동" />
        <KpiCard
          label="이상"
          value={cameras.length - normal}
          sub="오프라인"
          variant={cameras.length - normal ? "warn" : "default"}
        />
        <KpiCard
          label="금일 이벤트"
          value={cameras.reduce((sum, c) => sum + c.today, 0)}
          sub="전 카메라 합계"
        />
      </div>

      {/* 왼쪽에 목록, 오른쪽에 라이브. 목록을 보며 고르고 바로 옆에서 확인하는 흐름이라
        * 둘을 위아래로 두면 시선이 화면 밖까지 오르내린다. */}
      <div className="grid gap-5 xl:[grid-template-columns:minmax(0,1fr)_minmax(0,1fr)]">
      <Section
        title="카메라 목록"
        desc="물리 카메라 상태 및 솔루션 매핑"
        actions={<Tabs tabs={FILTERS} active={filter} onChange={setFilter} />}
      >
        <Card>
          <Table
            head={[
              "상태",
              "카메라",
              "설치 위치",
              "IP",
              "최근 수신",
              "오늘 / 누적",
              "녹화",
              "",
            ]}
          >
            {isLoading ? (
              <EmptyRow colSpan={8} text="불러오는 중…" />
            ) : shown.length === 0 ? (
              <EmptyRow colSpan={8} text="조건에 맞는 카메라가 없습니다." />
            ) : (
              shown.map((c) => (
                <tr
                  key={c.id}
                  className={cx(
                    "transition-colors hover:bg-surface-soft",
                    focused === c.id && "bg-primary-soft/40",
                  )}
                >
                  <Td>
                    <span className="inline-flex items-center gap-2 whitespace-nowrap">
                      <Dot ok={c.status === "normal"} />
                      <span className={c.status === "normal" ? "text-body" : "text-error"}>
                        {c.status === "normal" ? "정상" : "오프라인"}
                      </span>
                    </span>
                  </Td>
                  <Td className="font-medium text-body-strong">{c.name}</Td>
                  <Td>{c.location}</Td>
                  <Td className="tnum">{c.ip}</Td>
                  <Td className="whitespace-nowrap text-muted">
                    {c.last_seen_at ? fmtAgo(c.last_seen_at) : "-"}
                  </Td>
                  <Td className="tnum whitespace-nowrap">
                    {c.today} / {c.total}
                  </Td>
                  <Td>
                    <RecordToggle camera={c} />
                  </Td>
                  <Td className="text-right">
                    <Button size="sm" onClick={() => setEditing(c)}>
                      수정
                    </Button>
                  </Td>
                </tr>
              ))
            )}
          </Table>
          {shown.some((c) => c.last_error) && (
            <p className="mt-4 text-[12px] text-muted">
              오프라인 사유는 관리자 → 시스템 상태에서 확인할 수 있습니다.
            </p>
          )}
        </Card>
      </Section>

      {/* 큰 화면 하나 + 그 아래 가로 줄. 카메라가 늘면 아래 줄이 옆으로 스크롤된다.
        * 전부를 같은 크기로 늘어놓으면 대수가 늘수록 하나도 제대로 안 보인다. */}
      <Section title="라이브 확인" desc="아래에서 카메라를 고르면 크게 보입니다">
        {cameras.length === 0 ? (
          <Card className="py-14 text-center text-[13px] text-muted-soft">
            등록된 카메라가 없습니다.
          </Card>
        ) : (
          <div className="flex flex-col gap-3">
            {focusedCam ? (
              <LiveVideo
                camera={focusedCam}
                boxes={liveBoxes[focusedCam.id]}
                now={now}
                overlayTitle={focusedCam.location || focusedCam.name}
              />
            ) : (
              <Card className="flex aspect-video items-center justify-center text-[13px] text-muted-soft">
                카메라를 선택하세요
              </Card>
            )}
            {cameras.length > 1 && (
              <CameraStrip
                cameras={cameras}
                selectedId={focused}
                onSelect={setFocused}
                orientation="horizontal"
              />
            )}
          </div>
        )}
      </Section>
      </div>

      <QuickEdit camera={editing} onClose={() => setEditing(null)} />
    </>
  );
}

/** 카메라별 녹화 켜고 끄기.
 *
 * 관리자 폼 안쪽에만 두었더니 켜져 있는지조차 눈에 띄지 않아, 하룻밤에 1.6GB 가 쌓이는 것을
 * 아무도 모르는 일이 있었다. 현장에서 가장 자주 손대는 스위치라 목록에서 바로 만지게 한다.
 * 보존 시간처럼 한 번 정하고 두는 값은 관리자 화면에 남긴다.
 */
function RecordToggle({ camera }: { camera: Camera }) {
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (on: boolean) => api.patchCamera(camera.id, { record_enabled: on }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cameras"] }),
  });

  const on = camera.record_enabled;
  return (
    <div className="flex items-center gap-2 whitespace-nowrap">
      <button
        type="button"
        disabled={save.isPending}
        onClick={() => save.mutate(!on)}
        aria-pressed={on}
        title={on ? "녹화를 끕니다" : "녹화를 켭니다"}
        className={cx(
          "relative h-[19px] w-[34px] shrink-0 rounded-full transition-colors",
          on ? "bg-primary" : "bg-hairline",
          save.isPending && "opacity-50",
        )}
      >
        <span
          className={cx(
            "absolute top-[2px] h-[15px] w-[15px] rounded-full bg-canvas transition-all",
            on ? "left-[17px]" : "left-[2px]",
          )}
        />
      </button>
      <span className={cx("text-[12px]", on ? "text-body" : "text-muted-soft")}>
        {on ? fmtHours(camera.record_retention_hours) : "안 함"}
      </span>
    </div>
  );
}

/** 이름·설치 위치만 고치는 가벼운 편집. 구조적 설정은 관리자 화면으로 보낸다. */
function QuickEdit({ camera, onClose }: { camera: Camera | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [location, setLocation] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setName(camera?.name ?? "");
    setLocation(camera?.location ?? "");
    setError("");
  }, [camera?.id]);

  const save = useMutation({
    mutationFn: () => api.patchCamera(camera!.id, { name: name.trim(), location: location.trim() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cameras"] });
      onClose();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "저장 중 오류가 발생했습니다."),
  });

  if (!camera) return null;

  return (
    <Modal
      open
      onClose={onClose}
      title={`카메라 #${camera.id} 수정`}
      width={480}
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
      <div className="grid gap-4">
        <Field label="카메라 이름">
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="설치 위치" hint="통계·이벤트 목록에서 카메라를 식별하는 이름입니다.">
          <Input value={location} onChange={(e) => setLocation(e.target.value)} />
        </Field>
        <div className="rounded-lg border border-hairline bg-surface-soft px-4 py-3 text-[12.5px] text-muted">
          IP·계정·솔루션 매핑 변경은 <b className="font-semibold text-body">관리자</b> 화면에서
          할 수 있습니다.
        </div>
      </div>
    </Modal>
  );
}

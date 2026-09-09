/** 종합 현황 — 관제 화면의 첫 페이지.
 *
 * 카메라 현황 화면과 겹치지 않게 성격을 갈랐다.
 *   종합 현황  : 한 화면에서 '지금 무엇이 보이고 무슨 일이 있었나' 를 본다. 하나를 크게 본다.
 *   카메라 현황 : 등록된 카메라를 목록으로 관리한다.
 *
 * 배치
 *   왼쪽 절반  : 필름 스트립(작은 미리보기가 세로로 쌓임) + 고른 카메라의 큰 화면
 *   오른쪽 절반 : KPI 를 세로로 쌓고 아래는 비워 둔다(나중에 채울 자리)
 *   아래       : 최근 이벤트
 *
 * 스트립은 스냅샷, 큰 화면만 MJPEG 이다. 이유는 CameraStrip.tsx 주석 참조.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { CameraStrip } from "../components/CameraStrip";
import { EventDetail } from "../components/EventDetail";
import { LiveVideo } from "../components/LiveVideo";
import { Card, EmptyRow, Section, Table, Td, cx } from "../components/ui";
import { fmtAgo, fmtShort } from "../lib/format";
import { useCameras, useClock, useEvents, useLiveBoxes, useSummary } from "../lib/hooks";
import type { SafetyEvent } from "../lib/types";

/** 세로로 쌓이므로 카드가 아니라 가로형으로 만든다(같은 높이에 더 많이 들어간다). */
function KpiRow({
  label,
  value,
  sub,
  accent,
  onClick,
}: {
  label: string;
  value: number | string;
  sub?: string;
  accent?: "warn" | "primary";
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx(
        "flex w-full shrink-0 items-center justify-between gap-3 rounded-lg border border-hairline bg-canvas px-[18px] py-[13px] text-left transition-colors",
        onClick && "hover:border-primary/50 hover:bg-surface-soft",
      )}
    >
      <div className="min-w-0">
        <div className="text-[12.5px] font-medium text-body">{label}</div>
        {sub && <div className="mt-[2px] text-[11px] text-muted-soft">{sub}</div>}
      </div>
      <div
        className={cx(
          "tnum shrink-0 text-[26px] font-semibold leading-none",
          accent === "warn"
            ? "text-warning"
            : accent === "primary"
              ? "text-primary-active"
              : "text-ink",
        )}
      >
        {value}
      </div>
    </button>
  );
}

export function Dashboard() {
  const navigate = useNavigate();
  const now = useClock();
  const [selected, setSelected] = useState<SafetyEvent | null>(null);
  const [camId, setCamId] = useState<number | null>(null);

  const { data: summary } = useSummary();
  const { data: cameras = [] } = useCameras();
  const { data: recent } = useEvents({ limit: 10 });
  const liveBoxes = useLiveBoxes();

  // 처음 열렸을 때, 또는 보고 있던 카메라가 사라졌을 때 첫 카메라를 고른다.
  useEffect(() => {
    if (cameras.length === 0) {
      if (camId !== null) setCamId(null);
      return;
    }
    if (camId === null || !cameras.some((c) => c.id === camId)) {
      setCamId(cameras[0].id);
    }
  }, [cameras, camId]);

  const current = cameras.find((c) => c.id === camId) ?? null;
  const events = recent?.items ?? [];

  return (
    <>
      <div className="mb-7 grid gap-5 [grid-template-columns:1fr] xl:[grid-template-columns:1fr_1fr]">
        {/* ── 왼쪽 절반: 필름 스트립 + 큰 화면 ───────────────────────── */}
        <Section
          title="실시간 영상"
          desc="왼쪽에서 카메라를 고르면 크게 보입니다"
          className="mb-0"
        >
          {cameras.length === 0 ? (
            <Card className="flex h-[420px] items-center justify-center text-center text-[13px] text-muted-soft">
              <span>
                등록된 카메라가 없습니다.
                <br />
                관리자 화면에서 카메라 IP 를 등록하세요.
              </span>
            </Card>
          ) : (
            <div className="grid h-[420px] gap-3 [grid-template-columns:132px_minmax(0,1fr)]">
              <CameraStrip cameras={cameras} selectedId={camId} onSelect={setCamId} />
              {current ? (
                <LiveVideo
                  camera={current}
                  boxes={liveBoxes[current.id]}
                  now={now}
                  fill
                  onClick={() => navigate("/cameras")}
                />
              ) : (
                <Card className="flex items-center justify-center text-[13px] text-muted-soft">
                  카메라를 선택하세요
                </Card>
              )}
            </div>
          )}
        </Section>

        {/* ── 오른쪽 절반: 현황 셀(왼쪽) + LLM 자리(오른쪽) ─────────── */}
        <Section title="현황" desc="누르면 해당 목록으로 이동합니다" className="mb-0">
          <div className="grid h-[420px] gap-3 [grid-template-columns:minmax(0,1fr)_minmax(0,1fr)]">
          <div className="flex min-h-0 flex-col gap-[10px] overflow-y-auto pr-1">
            <KpiRow
              label="등록 카메라"
              value={summary?.cameras_total ?? 0}
              sub="전체"
              onClick={() => navigate("/cameras")}
            />
            <KpiRow
              label="정상 카메라"
              value={summary?.cameras_normal ?? 0}
              sub="영상 수신 중"
              onClick={() => navigate("/cameras", { state: { filter: "normal" } })}
            />
            <KpiRow
              label="이상 카메라"
              value={summary?.cameras_offline ?? 0}
              sub="오프라인"
              accent={summary?.cameras_offline ? "warn" : undefined}
              onClick={() => navigate("/cameras", { state: { filter: "offline" } })}
            />
            <KpiRow
              label="금일 이벤트"
              value={summary?.events_today ?? 0}
              sub="오늘 발생"
              accent={summary?.events_today ? "primary" : undefined}
              onClick={() => navigate("/events", { state: { preset: "today" } })}
            />
            <KpiRow
              label="주간 이벤트"
              value={summary?.events_week ?? 0}
              sub="최근 7일"
              onClick={() => navigate("/events", { state: { preset: "week" } })}
            />
            <KpiRow
              label="월간 이벤트"
              value={summary?.events_month ?? 0}
              sub="최근 30일"
              onClick={() => navigate("/events", { state: { preset: "month" } })}
            />
          </div>

          {/* LLM 이 들어올 자리.
            *
            * 축적된 이벤트를 읽어 요약·질의응답을 하는 부분이 여기 붙는다. 지금 비워 두되
            * 무엇이 올 자리인지는 적어 둔다 — 점선 빈 칸만 있으면 '덜 만든 화면' 으로 보이고,
            * 나중에 붙일 때 크기가 맞지 않아 옆 셀까지 다시 짜게 된다. */}
          <div className="flex min-h-0 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-hairline bg-surface-soft/40 px-5 text-center">
            <span className="text-[13px] font-semibold text-body-strong">AI 요약</span>
            <span className="text-[12px] leading-relaxed text-muted-soft">
              쌓인 이벤트를 읽어 오늘의 상황을 정리하고 물어볼 수 있는 자리입니다.
              <br />
              아직 붙이지 않았습니다.
            </span>
          </div>
          </div>
        </Section>
      </div>

      <Section title="최근 이벤트" desc="가장 최근에 발생한 순서">
        <Card>
          <Table head={["발생시간", "카메라", "이벤트", "자료"]}>
            {events.length === 0 ? (
              <EmptyRow
                colSpan={4}
                text="아직 이벤트가 없습니다. 탐지 항목과 바인딩이 정해지면 여기에 쌓입니다."
              />
            ) : (
              events.map((e) => (
                <tr
                  key={e.id}
                  onClick={() => setSelected(e)}
                  className="cursor-pointer transition-colors hover:bg-surface-soft"
                >
                  <Td className="tnum whitespace-nowrap">
                    {fmtShort(e.ts)}
                    <span className="ml-2 text-[11.5px] text-muted-soft">{fmtAgo(e.ts)}</span>
                  </Td>
                  <Td>{e.cam_location}</Td>
                  <Td>{e.type}</Td>
                  <Td className="text-muted">
                    {e.has_clip ? "영상 · 사진" : e.has_snapshot ? "사진" : "-"}
                  </Td>
                </tr>
              ))
            )}
          </Table>
        </Card>
      </Section>

      <EventDetail event={selected} onClose={() => setSelected(null)} />
    </>
  );
}

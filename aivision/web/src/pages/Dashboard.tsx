/** 종합 현황 — 관제 화면의 첫 페이지.
 *
 * 카메라 현황 화면과 겹치지 않게 성격을 갈랐다.
 *   종합 현황  : 한 화면에서 '지금 무엇이 보이고 무슨 일이 있었나' 를 본다. 하나를 크게 본다.
 *   카메라 현황 : 등록된 카메라를 목록으로 관리한다.
 *
 * 배치
 *   왼쪽 절반  : 필름 스트립(작은 미리보기가 세로로 쌓임) + 고른 카메라의 큰 화면
 *   오른쪽 절반 : KPI 를 세로로 쌓고, 그 옆에 가장 최근 이벤트의 스냅샷
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
import { api } from "../lib/api";
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
  // 목록은 최신순이므로 앞에서부터 사진이 있는 첫 건이 '가장 최근 스냅샷' 이다.
  const latestSnap = events.find((e) => e.has_snapshot) ?? null;

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

        {/* ── 오른쪽 절반: 현황 셀(왼쪽) + 최근 이벤트 스냅샷(오른쪽) ── */}
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

          {/* 가장 최근 이벤트의 스냅샷.
            *
            * 숫자만 있는 KPI 옆에 '그래서 무엇이 찍혔나' 를 붙인다. 운영자가 이벤트 목록을
            * 열어 한 건을 고르기 전에, 방금 무슨 일이 있었는지 한눈에 보게 하는 자리다.
            *
            * 맨 앞이 아니라 **스냅샷이 있는 것 중 맨 앞**을 고른다. 최신 이벤트에 사진이
            * 없을 때 빈 칸을 보여 주면 '고장난 화면' 으로 보이기 때문이다. 대신 발생 시각을
            * 함께 적어 언제 것인지 감추지 않는다.
            *
            * 박스는 그리지 않는다. 이 칸은 작아서 선이 겹치면 사진이 안 보인다 —
            * 박스까지 보려면 눌러서 상세를 연다(아래 최근 이벤트 표와 같은 창). */}
          {latestSnap ? (
            <button
              type="button"
              onClick={() => setSelected(latestSnap)}
              className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-hairline bg-surface-soft/40 text-left transition-colors hover:border-strong"
            >
              <div className="flex items-baseline justify-between gap-2 px-3 py-2">
                <span className="text-[13px] font-semibold text-body-strong">최근 이벤트</span>
                <span className="tnum shrink-0 text-[11.5px] text-muted-soft">
                  {fmtAgo(latestSnap.ts)}
                </span>
              </div>
              <div className="min-h-0 flex-1 bg-video-bg">
                <img
                  src={api.snapshotUrl(latestSnap.id)}
                  alt={`${latestSnap.id} 캡쳐`}
                  className="h-full w-full object-contain"
                />
              </div>
              <div className="px-3 py-2">
                <div className="truncate text-[12.5px] font-semibold text-body-strong">
                  {latestSnap.type}
                </div>
                <div className="tnum truncate text-[11.5px] text-muted-soft">
                  {latestSnap.cam_location} · {fmtShort(latestSnap.ts)}
                </div>
              </div>
            </button>
          ) : (
            <div className="flex min-h-0 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-hairline bg-surface-soft/40 px-5 text-center">
              <span className="text-[13px] font-semibold text-body-strong">최근 이벤트</span>
              <span className="text-[12px] leading-relaxed text-muted-soft">
                {events.length === 0
                  ? "아직 이벤트가 없습니다."
                  : "최근 이벤트에 저장된 사진이 없습니다."}
              </span>
            </div>
          )}
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

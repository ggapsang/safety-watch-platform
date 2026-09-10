/** 통계 — 기간별 발생 분석.
 *
 * 서버(`services/stats.py`)가 구간별 시계열과 항목별·카메라별 집계를 이미 완성해서
 * 내려준다. 여기서는 다시 세지 않는다 — 같은 수를 두 곳에서 세면 언젠가 화면과 CSV 가
 * 다른 값을 말한다.
 *
 * 차트 옵션은 `lib/charts.ts` 에 모아 둔 것을 쓴다. 시안(refs/demo)의 색·눈금·툴팁을
 * 옮겨 둔 것이라, 여기서 옵션을 따로 만들면 화면마다 질감이 갈린다.
 *
 * 분포를 도넛이 아니라 가로 막대로 두는 이유: 항목이 늘어도 이름이 잘리지 않고, 건수를
 * 눈금으로 바로 읽을 수 있다. 도넛은 조각이 셋만 넘어가도 어느 것이 큰지 헷갈린다.
 */
import { useState } from "react";

import { Bar, Line } from "react-chartjs-2";

import { Card, CardTitle, EmptyRow, Field, Input, Section, Table, Tabs, Td } from "../components/ui";
import { areaFill, barOptions, lineOptions } from "../lib/charts";
import { isoDate } from "../lib/format";
import { useStats } from "../lib/hooks";
import type { Bucket } from "../lib/types";

const TABS: { id: Bucket; label: string }[] = [
  { id: "hourly", label: "시간대별" },
  { id: "daily", label: "일별" },
  { id: "weekly", label: "주별" },
  { id: "monthly", label: "월별" },
];

/** 시안의 강조색. 탐지 항목이 자기 색을 들고 오지 않을 때만 쓴다. */
const PRIMARY = "#cc785c";

/** 차트 자리. 불러오는 중과 '한 건도 없음'을 갈라 준다 —
 *  둘을 같은 빈 칸으로 보여 주면 고장인지 데이터가 없는 것인지 알 수 없다. */
function ChartBox({
  title,
  desc,
  height,
  empty,
  loading,
  children,
}: {
  title: string;
  desc: string;
  height: number;
  empty: boolean;
  loading: boolean;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardTitle title={title} desc={desc} />
      <div style={{ height }} className="relative">
        {loading || empty ? (
          <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-hairline bg-surface-soft/60 text-[12.5px] text-muted-soft">
            {loading ? "불러오는 중…" : "이 기간에는 발생한 이벤트가 없습니다."}
          </div>
        ) : (
          children
        )}
      </div>
    </Card>
  );
}

export function Stats() {
  const [bucket, setBucket] = useState<Bucket>("daily");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");

  // 빈 문자열을 그대로 보내면 서버가 잘못된 날짜로 읽는다. 채운 것만 넘긴다.
  const { data, isLoading } = useStats({
    bucket,
    ...(start ? { start } : {}),
    ...(end ? { end } : {}),
  });

  const series = data?.series ?? [];
  const bySolution = data?.by_solution ?? [];
  const byCamera = data?.by_camera ?? [];
  const total = data?.total ?? 0;

  // 구간이 다 0 이면 선을 그려도 읽을 것이 없다. '없음'으로 보여 준다.
  const seriesEmpty = series.every((p) => p.count === 0);

  return (
    <>
      <Section
        title="기간별 통계"
        desc={
          isLoading
            ? "불러오는 중…"
            : `선택한 기간에 ${total.toLocaleString()}건이 발생했습니다.`
        }
        actions={
          <div className="flex items-end gap-3">
            <Field label="시작일">
              <Input
                type="date"
                value={start}
                max={end || isoDate()}
                onChange={(e) => setStart(e.target.value)}
              />
            </Field>
            <Field label="종료일">
              <Input type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
            </Field>
          </div>
        }
      >
        <Tabs tabs={TABS} active={bucket} onChange={setBucket} />

        <div className="grid gap-5 [grid-template-columns:1fr] xl:[grid-template-columns:1.4fr_1fr]">
          <ChartBox
            title="발생 추이"
            desc="선택한 구간의 시계열"
            height={300}
            loading={isLoading}
            empty={series.length === 0 || seriesEmpty}
          >
            <Line
              options={lineOptions}
              data={{
                labels: series.map((p) => p.label),
                datasets: [
                  {
                    label: "발생",
                    data: series.map((p) => p.count),
                    borderColor: PRIMARY,
                    backgroundColor: areaFill,
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    tension: 0.35,
                    fill: true,
                  },
                ],
              }}
            />
          </ChartBox>

          <ChartBox
            title="항목별 분포"
            desc="탐지 항목별 건수"
            height={300}
            loading={isLoading}
            empty={bySolution.length === 0}
          >
            <Bar
              options={barOptions}
              data={{
                labels: bySolution.map((s) => s.short_name),
                datasets: [
                  {
                    label: "건수",
                    // 항목이 자기 색을 들고 온다(solutions.color). 화면에서 색을 정하면
                    // 이벤트 목록·통계가 서로 다른 색으로 같은 항목을 가리키게 된다.
                    data: bySolution.map((s) => s.count),
                    backgroundColor: bySolution.map((s) => s.color || PRIMARY),
                    borderRadius: 4,
                    barThickness: 18,
                  },
                ],
              }}
            />
          </ChartBox>
        </div>
      </Section>

      <Section title="카메라별 집계" desc="설치 위치 기준">
        <div className="grid gap-5 [grid-template-columns:1fr] xl:[grid-template-columns:1fr_1fr]">
          <ChartBox
            title="카메라별 발생"
            desc="상위 카메라"
            height={280}
            loading={isLoading}
            empty={byCamera.length === 0}
          >
            <Bar
              options={barOptions}
              data={{
                // 서버가 건수 내림차순으로 주므로 상위 8대만 자른다. 카메라가 수십 대가
                // 되면 막대가 실처럼 얇아져 아무것도 읽히지 않는다. 전체는 옆 표에 있다.
                labels: byCamera.slice(0, 8).map((c) => c.location || c.name),
                datasets: [
                  {
                    label: "건수",
                    data: byCamera.slice(0, 8).map((c) => c.count),
                    backgroundColor: PRIMARY,
                    borderRadius: 4,
                    barThickness: 18,
                  },
                ],
              }}
            />
          </ChartBox>

          <Card>
            <CardTitle title="상세 표" desc="카메라 · 위치 · 건수" />
            <Table head={["카메라", "설치 위치", "건수"]}>
              {byCamera.length === 0 ? (
                <EmptyRow
                  colSpan={3}
                  text={isLoading ? "불러오는 중…" : "이 기간에는 발생한 이벤트가 없습니다."}
                />
              ) : (
                byCamera.map((c) => (
                  <tr key={c.camera_id}>
                    <Td>{c.name}</Td>
                    <Td className="text-muted">{c.location}</Td>
                    <Td className="tnum">{c.count.toLocaleString()}</Td>
                  </tr>
                ))
              )}
            </Table>
          </Card>
        </div>
      </Section>
    </>
  );
}

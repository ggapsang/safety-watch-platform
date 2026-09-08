/** 통계 — 껍데기.
 *
 * 무엇을 세고 무엇을 축으로 볼지는 탐지 항목이 정해진 뒤에 결정한다. 지금 그럴듯한 지표를
 * 만들어 두면 나중에 그것부터 지워야 하므로, 화면 골격과 조회 조건만 세워 두고 비워 둔다.
 *
 * 골격은 이미 서 있다.
 *   · 기간 구간(시간/일/주/월)과 날짜 범위 선택
 *   · 서버 /api/stats 는 구간별 시계열·카메라별 집계를 이미 내려준다
 *     (services/stats.py). 여기에 카드를 붙이기만 하면 된다.
 */
import { useState } from "react";

import { Card, Field, Input, Section, Tabs } from "../components/ui";
import { isoDate } from "../lib/format";
import type { Bucket } from "../lib/types";

const TABS: { id: Bucket; label: string }[] = [
  { id: "hourly", label: "시간대별" },
  { id: "daily", label: "일별" },
  { id: "weekly", label: "주별" },
  { id: "monthly", label: "월별" },
];

function Placeholder({ title, desc, height = 280 }: { title: string; desc: string; height?: number }) {
  return (
    <Card>
      <div className="mb-4">
        <div className="text-[14.5px] font-semibold tracking-tight text-ink">{title}</div>
        <div className="mt-1 text-[12px] text-muted">{desc}</div>
      </div>
      <div
        style={{ height }}
        className="flex items-center justify-center rounded-lg border border-dashed border-hairline bg-surface-soft/60 text-[12.5px] text-muted-soft"
      >
        준비 중
      </div>
    </Card>
  );
}

export function Stats() {
  const [bucket, setBucket] = useState<Bucket>("daily");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");

  return (
    <>
      <Section
        title="기간별 통계"
        desc="조회 조건입니다. 표시할 지표는 탐지 항목이 정해진 뒤에 채웁니다."
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
          <Placeholder title="발생 추이" desc="선택한 구간의 시계열" height={300} />
          <Placeholder title="항목별 분포" desc="탐지 항목이 정해지면 표시됩니다" height={300} />
        </div>
      </Section>

      <Section title="카메라별 집계" desc="설치 위치 기준">
        <div className="grid gap-5 [grid-template-columns:1fr] xl:[grid-template-columns:1fr_1fr]">
          <Placeholder title="카메라별 발생" desc="상위 카메라" />
          <Placeholder title="상세 표" desc="카메라 · 위치 · 건수" />
        </div>
      </Section>
    </>
  );
}

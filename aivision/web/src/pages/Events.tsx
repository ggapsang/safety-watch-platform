/** 이벤트 이력 — 조회와 엑셀 내려받기.
 *
 * 필터는 날짜·카메라·검색 셋뿐이다. 심각도나 처리 상태 같은 분류는 기준이 정해지지 않아
 * 넣지 않았다. 기준이 서면 그때 항목을 추가한다.
 */
import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";

import { EventDetail } from "../components/EventDetail";
import { IconDownload } from "../components/icons";
import { Button, Card, EmptyRow, Field, Input, Section, Select, Table, Td } from "../components/ui";
import { api } from "../lib/api";
import { fmtDateTime, isoDate } from "../lib/format";
import { useCameras, useEvents } from "../lib/hooks";
import type { SafetyEvent } from "../lib/types";

const PAGE = 50;

export function Events() {
  const { state } = useLocation() as { state?: { preset?: "today" | "week" | "month" } };
  const { data: cameras = [] } = useCameras();

  const today = isoDate();
  const monthAgo = isoDate(new Date(Date.now() - 30 * 86400_000));

  const [start, setStart] = useState(monthAgo);
  const [end, setEnd] = useState(today);
  const [camId, setCamId] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<SafetyEvent | null>(null);

  useEffect(() => {
    // 종합 현황의 셀에서 넘어올 때 기간을 맞춰 준다. 셀에 30 이라고 떠 있는데
    // 목록에는 오늘 것만 보이면 숫자가 틀린 것처럼 보인다.
    const spans: Record<string, number> = { today: 0, week: 6, month: 29 };
    const days = state?.preset ? spans[state.preset] : undefined;
    if (days !== undefined) {
      const from = new Date();
      from.setDate(from.getDate() - days);
      setStart(isoDate(from));
      setEnd(today);
    }
    // 진입 시 한 번만 적용한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.preset]);

  const query = useMemo(
    () => ({
      start: `${start}T00:00:00`,
      end: `${end}T23:59:59`,
      cameras: camId ? [camId] : undefined,
      q: q.trim() || undefined,
      limit: PAGE,
      offset: page * PAGE,
    }),
    [start, end, camId, q, page],
  );

  const { data, isLoading } = useEvents(query);
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const maxPage = Math.max(0, Math.ceil(total / PAGE) - 1);

  const reset = () => {
    setStart(monthAgo);
    setEnd(today);
    setCamId(null);
    setQ("");
    setPage(0);
  };

  // 조회 조건이 바뀌면 첫 페이지로 돌아간다(안 그러면 빈 페이지가 보인다).
  useEffect(() => setPage(0), [start, end, camId, q]);

  return (
    <>
      <Section
        title="조회 조건"
        desc="기간과 카메라로 좁히고, 검색어로 이벤트 ID·유형을 찾습니다."
        actions={
          <>
            <Button size="sm" onClick={reset}>
              초기화
            </Button>
            <a href={api.exportUrl({ ...query, limit: undefined, offset: undefined })} download>
              <Button size="sm" variant="primary">
                <IconDownload size={13} />
                엑셀 내려받기
              </Button>
            </a>
          </>
        }
      >
        <Card>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Field label="시작일">
              <Input
                type="date"
                value={start}
                max={end}
                onChange={(e) => setStart(e.target.value)}
              />
            </Field>
            <Field label="종료일">
              <Input type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
            </Field>
            <Field label="카메라">
              <Select
                value={camId ?? ""}
                onChange={(e) => setCamId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">전체</option>
                {cameras.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} · {c.location}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="검색" hint="이벤트 ID · 유형">
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="검색어" />
            </Field>
          </div>
        </Card>
      </Section>

      <Section title="이벤트 이력" desc={`총 ${total.toLocaleString()}건`}>
        <Card>
          <Table head={["이벤트 ID", "발생 시간", "카메라", "설치 위치", "이벤트", "캡쳐"]}>
            {isLoading ? (
              <EmptyRow colSpan={6} text="불러오는 중…" />
            ) : items.length === 0 ? (
              <EmptyRow colSpan={6} text="조건에 맞는 이벤트가 없습니다." />
            ) : (
              items.map((e) => (
                <tr
                  key={e.id}
                  onClick={() => setSelected(e)}
                  className="cursor-pointer transition-colors hover:bg-surface-soft"
                >
                  <Td className="tnum whitespace-nowrap font-medium text-body-strong">{e.id}</Td>
                  <Td className="tnum whitespace-nowrap">{fmtDateTime(e.ts)}</Td>
                  <Td>{e.cam_name}</Td>
                  <Td>{e.cam_location}</Td>
                  <Td>{e.type}</Td>
                  <Td className="text-muted">{e.has_snapshot ? "있음" : "-"}</Td>
                </tr>
              ))
            )}
          </Table>

          {maxPage > 0 && (
            <div className="mt-5 flex items-center justify-center gap-3 text-[12.5px] text-muted">
              <Button size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                이전
              </Button>
              <span className="tnum">
                {page + 1} / {maxPage + 1}
              </span>
              <Button size="sm" disabled={page >= maxPage} onClick={() => setPage((p) => p + 1)}>
                다음
              </Button>
            </div>
          )}
        </Card>
      </Section>

      <EventDetail event={selected} onClose={() => setSelected(null)} />
    </>
  );
}

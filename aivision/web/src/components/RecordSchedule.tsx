/** 요일·시간대 자동 녹화 편집기.
 *
 * 구간을 여러 개 둘 수 있게 한 이유: 현장 요구가 '평일 주간' 하나로 안 끝난다. 주간은
 * 평일만, 야간은 금요일만, 주말은 종일 같은 조합이 실제로 나온다. 하나만 두면 그 순간
 * 스케줄을 포기하고 늘 켜 두게 된다.
 *
 * **시각은 현장 시간대로 읽습니다**(서버 TZ, 기본 Asia/Seoul). 벽시계를 보고 적는 값이라
 * 그래야 하고, 화면에도 그렇게 적어 둔다 — 안 적으면 UTC 인지 로컬인지 물어보게 된다.
 *
 * 저장 모양은 서버 계약 그대로다.
 *   {"windows": [{"days": [0,1,2,3,4], "start": "08:00", "end": "18:00"}]}
 *   days 는 0=월 … 6=일.
 */
import type { RecordSchedule as Schedule, RecordWindow } from "../lib/types";
import { Button, cx } from "./ui";

const DAYS = ["월", "화", "수", "목", "금", "토", "일"];

const WEEKDAYS = [0, 1, 2, 3, 4];
const WEEKEND = [5, 6];
const EVERYDAY = [0, 1, 2, 3, 4, 5, 6];

const NEW_WINDOW: RecordWindow = { days: WEEKDAYS, start: "08:00", end: "18:00" };

export function RecordSchedule({
  value,
  onChange,
}: {
  value: Schedule | null;
  onChange: (next: Schedule | null) => void;
}) {
  const windows = value?.windows ?? [];

  // 구간이 하나도 없으면 null 로 돌려준다. 빈 배열을 저장하면 '스케줄을 쓰는데 구간이
  // 없다' 는 상태가 되어, 서버는 '항상' 으로 읽고 사람은 '안 됨' 으로 읽는다.
  const put = (next: RecordWindow[]) => onChange(next.length ? { windows: next } : null);
  const patch = (i: number, part: Partial<RecordWindow>) =>
    put(windows.map((w, j) => (j === i ? { ...w, ...part } : w)));

  const toggleDay = (i: number, day: number) => {
    const days = windows[i].days.includes(day)
      ? windows[i].days.filter((d) => d !== day)
      : [...windows[i].days, day].sort((a, b) => a - b);
    // 요일을 다 지우면 서버가 '매일' 로 읽는다. 사람이 뜻한 것은 대개 그 반대이므로
    // 마지막 하나는 못 끄게 한다.
    if (days.length) patch(i, { days });
  };

  return (
    <div className="mt-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[12.5px] font-semibold text-body-strong">자동 녹화 시간</span>
        <span className="text-[11.5px] text-muted-soft">
          {windows.length === 0
            ? "비워 두면 상시 녹화가 켜져 있는 동안 항상 녹화합니다"
            : "이 시간에만 녹화합니다 · 현장 시간(KST) 기준"}
        </span>
      </div>

      <div className="grid gap-2">
        {windows.map((w, i) => (
          <div
            key={i}
            className="rounded-lg border border-hairline bg-surface-soft/40 px-3 py-[10px]"
          >
            <div className="mb-2 flex flex-wrap items-center gap-[6px]">
              {DAYS.map((label, day) => (
                <button
                  key={day}
                  type="button"
                  onClick={() => toggleDay(i, day)}
                  className={cx(
                    "h-[26px] w-[26px] rounded-md border text-[12px] font-semibold transition-colors",
                    w.days.includes(day)
                      ? "border-primary bg-primary text-white"
                      : "border-hairline bg-canvas text-muted hover:border-primary",
                  )}
                >
                  {label}
                </button>
              ))}
              <div className="ml-1 flex gap-1">
                <Chip onClick={() => patch(i, { days: WEEKDAYS })}>평일</Chip>
                <Chip onClick={() => patch(i, { days: WEEKEND })}>주말</Chip>
                <Chip onClick={() => patch(i, { days: EVERYDAY })}>매일</Chip>
              </div>
              <div className="flex-1" />
              <Button size="sm" onClick={() => put(windows.filter((_, j) => j !== i))}>
                빼기
              </Button>
            </div>

            <div className="flex items-center gap-2">
              <input
                type="time"
                value={w.start}
                onChange={(e) => patch(i, { start: e.target.value })}
                className="rounded-md border border-hairline bg-canvas px-2 py-[5px] text-[13px] tnum"
              />
              <span className="text-[12.5px] text-muted">~</span>
              <input
                type="time"
                value={w.end}
                onChange={(e) => patch(i, { end: e.target.value })}
                className="rounded-md border border-hairline bg-canvas px-2 py-[5px] text-[13px] tnum"
              />
              {w.start > w.end && (
                // 자정을 넘는 구간. 요일이 '시작한 날' 을 가리킨다는 것을 여기서 알린다 —
                // 금요일 22시로 적어 두고 토요일 새벽이 안 찍힐까 걱정하는 일이 없도록.
                <span className="text-[11.5px] text-primary-active">
                  자정을 넘습니다 — 고른 요일에 <b>시작</b>해 다음 날 {w.end} 까지
                </span>
              )}
              {w.start === w.end && (
                <span className="text-[11.5px] text-error">
                  시작과 끝이 같습니다 — 저장되지 않습니다
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      <Button
        size="sm"
        className="mt-2"
        onClick={() => put([...windows, { ...NEW_WINDOW }])}
      >
        + 시간대 추가
      </Button>
    </div>
  );
}

function Chip({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-full border border-hairline px-[9px] py-[2px] text-[11.5px] text-muted transition-colors hover:border-primary hover:text-primary-active"
    >
      {children}
    </button>
  );
}

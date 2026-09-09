/** 앱 껍데기 — 좌측 내비게이션 + 상단 바.
 *
 * 좌상단은 DAIM 로고, 그 아래에 서비스명(AI Vision 통합관제 대시보드)이 온다.
 * 내비게이션은 '관제 / 분석 / 운영' 세 묶음이다.
 */
import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import {
  IconCamera,
  IconChart,
  IconDashboard,
  IconList,
  IconSettings,
  IconSignal,
} from "./icons";
import { fmtClock } from "../lib/format";
import { useClock, type LiveState } from "../lib/hooks";
import { cx } from "./ui";

interface NavItem {
  to: string;
  label: string;
  desc: string;
  icon: ReactNode;
}

const GROUPS: { group: string; items: NavItem[] }[] = [
  {
    group: "관제",
    items: [
      { to: "/", label: "종합 현황", desc: "실시간 영상과 이벤트", icon: <IconDashboard /> },
      {
        to: "/cameras",
        label: "카메라 현황",
        desc: "등록 카메라 상태 확인",
        icon: <IconCamera />,
      },
      { to: "/events", label: "이벤트 이력", desc: "이벤트 검색 및 상세 조회", icon: <IconList /> },
    ],
  },
  {
    group: "분석",
    items: [
      { to: "/stats", label: "통계", desc: "기간별 발생 분석", icon: <IconChart /> },
    ],
  },
  {
    // MQTT 로그는 평소에 보는 화면이 아니라 '무엇이 들어오는지 확인할 때' 여는 점검 도구다.
    // 그래서 분석이 아니라 운영에, 그중에서도 맨 아래에 둔다.
    group: "운영",
    items: [
      { to: "/admin", label: "관리자", desc: "카메라 등록 · 시스템 상태", icon: <IconSettings /> },
      { to: "/mqtt", label: "MQTT 로그", desc: "실시간 MQTT 수신 메시지", icon: <IconSignal /> },
    ],
  },
];

const ALL = GROUPS.flatMap((g) => g.items);

export function Shell({ live, children }: { live: LiveState; children: ReactNode }) {
  const now = useClock();
  const { pathname } = useLocation();
  const current = ALL.find((i) => i.to === pathname) ?? ALL[0];

  return (
    <div className="flex h-full min-h-screen bg-canvas">
      {/* 좌측 내비게이션 */}
      <aside className="sticky top-0 flex h-screen w-[248px] shrink-0 flex-col border-r border-hairline bg-surface-soft">
        <div className="px-[22px] pb-[18px] pt-[24px]">
          <img
            src="/daim-logo.png"
            alt="DAIM Technology"
            className="block w-full max-w-[168px]"
          />
          <div className="mt-[14px] text-[12.5px] tracking-[-.01em] text-muted">
            <b className="font-semibold text-ink">AI Vision</b> 통합관제 대시보드
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto px-[14px] py-[14px]">
          {GROUPS.map((g) => (
            <div key={g.group} className="mb-[22px]">
              <div className="px-3 pb-[10px] text-[11px] font-semibold uppercase tracking-[.12em] text-muted-soft">
                {g.group}
              </div>
              {g.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === "/"}
                  className={({ isActive }) =>
                    cx(
                      "relative flex items-center gap-[11px] rounded-md px-3 py-[10px] text-[14px] transition-colors duration-150",
                      isActive
                        ? "bg-surface-card font-semibold text-ink"
                        : "font-medium text-muted hover:bg-cream-strong/50 hover:text-body-strong",
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      {isActive && (
                        <span className="absolute left-[-14px] top-1/2 h-[20px] w-[3px] -translate-y-1/2 rounded-r-[3px] bg-primary" />
                      )}
                      <span className={cx("shrink-0", isActive ? "text-primary" : "opacity-70")}>
                        {item.icon}
                      </span>
                      {item.label}
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="flex items-center justify-between gap-[10px] border-t border-hairline-soft px-[22px] py-[16px] text-[12px] text-muted">
          <span className="flex min-w-0 items-center gap-2">
            <span
              className={cx(
                "h-[7px] w-[7px] shrink-0 rounded-full",
                live === "open" ? "bg-success" : live === "connecting" ? "bg-warning" : "bg-error",
              )}
            />
            <span className="truncate">
              {live === "open"
                ? "시스템 정상 운영 중"
                : live === "connecting"
                  ? "연결 중…"
                  : "서버 연결 끊김"}
            </span>
          </span>
        </div>
      </aside>

      {/* 본문 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-4 border-b border-hairline bg-canvas/95 px-8 py-[18px] backdrop-blur">
          <div className="min-w-0">
            <h1 className="text-[19px] font-semibold tracking-tight text-ink">{current.label}</h1>
            <p className="mt-[2px] text-[12.5px] text-muted">{current.desc}</p>
          </div>
          <div className="flex-1" />
          <div className="tnum text-[12.5px] text-muted">{fmtClock(now)}</div>
        </header>

        <main className="min-w-0 flex-1 px-8 py-7">{children}</main>
      </div>
    </div>
  );
}

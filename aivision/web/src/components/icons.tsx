/** 인라인 SVG 아이콘.
 *
 * 텍스트 글리프(문자표 기호)를 아이콘 대신 쓰지 않는다. 글꼴에 따라 모양·크기가 제각각이고,
 * 폰트가 없는 환경에서는 네모(tofu)로 깨진다. 선 굵기를 맞춘 SVG 로 통일한다.
 *
 * currentColor 를 쓰므로 색은 부모의 text-* 클래스를 따라간다.
 */
type IconProps = {
  size?: number;
  className?: string;
};

const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
});

/** 종합 현황 — 4분할 대시보드 */
export const IconDashboard = ({ size = 15, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <rect x="2" y="2" width="5" height="5" rx="1" />
    <rect x="9" y="2" width="5" height="5" rx="1" />
    <rect x="2" y="9" width="5" height="5" rx="1" />
    <rect x="9" y="9" width="5" height="5" rx="1" />
  </svg>
);

/** 카메라 현황 */
export const IconCamera = ({ size = 15, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M2 5.5a1.5 1.5 0 0 1 1.5-1.5h2L6.5 2.5h3L10.5 4h2A1.5 1.5 0 0 1 14 5.5v6A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5z" />
    <circle cx="8" cy="8.5" r="2.5" />
  </svg>
);

/** 이벤트 이력 — 목록 */
export const IconList = ({ size = 15, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4h.01M2.5 8h.01M2.5 12h.01" />
  </svg>
);

/** 통계 — 막대 그래프 */
export const IconChart = ({ size = 15, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M2.5 13.5V9M6.5 13.5V4M10.5 13.5V7M14 13.5v-3" />
  </svg>
);

/** MQTT 로그 — 신호 파형 */
export const IconSignal = ({ size = 15, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M1.5 8h2.5l2-4.5 3 9 2-4.5h2.5" />
  </svg>
);

/** 관리자 — 슬라이더 */
export const IconSettings = ({ size = 15, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M2.5 4.5h11M2.5 11.5h11" />
    <circle cx="6" cy="4.5" r="1.75" />
    <circle cx="10.5" cy="11.5" r="1.75" />
  </svg>
);

/** 닫기 */
export const IconClose = ({ size = 14, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />
  </svg>
);

/** 내려받기 */
export const IconDownload = ({ size = 14, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M8 2.5v7.5M4.5 7L8 10.5 11.5 7M2.5 13h11" />
  </svg>
);

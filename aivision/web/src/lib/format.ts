/** 표시 형식 — 시각 표기. 화면 여러 곳에서 같은 규칙을 쓰도록 한 곳에 모은다. */

const pad = (n: number) => String(n).padStart(2, "0");

export const toDate = (iso: string): Date => new Date(iso);

/** 2026-09-08 14:03:21 */
export const fmtDateTime = (iso: string | Date): string => {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(
    d.getMinutes(),
  )}:${pad(d.getSeconds())}`;
};

/** 09-08 14:03 — 표 안에서 쓰는 짧은 형식 */
export const fmtShort = (iso: string | Date): string => {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export const fmtClock = (d: Date): string =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(
    d.getMinutes(),
  )}:${pad(d.getSeconds())}`;

export const isoDate = (d: Date = new Date()): string =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/** "3분 전" — 최근 이벤트의 신선도를 한눈에 보이게 한다. */
export const fmtAgo = (iso: string): string => {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "방금";
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`;
  return `${Math.floor(diff / 86400)}일 전`;
};

/** 보존 시간을 사람이 읽는 말로. 72 -> "3일", 6 -> "6시간", 36 -> "1일 12시간". */
export const fmtHours = (hours: number): string => {
  const h = Math.max(0, Math.round(hours));
  if (h < 24) return `${h}시간`;
  const days = Math.floor(h / 24);
  const rest = h % 24;
  return rest === 0 ? `${days}일` : `${days}일 ${rest}시간`;
};

/** 바이트를 사람이 읽는 말로. 저장 용량은 GB 까지면 충분하다. */
export const fmtBytes = (bytes: number): string => {
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(1)}GB`;
  const mb = bytes / 1024 ** 2;
  if (mb >= 1) return `${mb.toFixed(0)}MB`;
  return `${Math.max(0, Math.round(bytes / 1024))}KB`;
};

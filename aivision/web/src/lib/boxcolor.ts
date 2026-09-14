/** 라이브 박스의 색을 정하는 곳 — 한 군데에서만 정한다.
 *
 * 한 카메라를 여러 플러그인이 함께 보면서 박스가 섞이기 시작했다. 전부 같은 색이면
 * 어느 것이 사람이고 어느 것이 AMR 이고 어느 것이 위험구역인지 그림만 보고는 알 수 없다.
 *
 * **기본은 자동이다.** 이름을 해싱해 팔레트에서 고른다. 새 플러그인이 새 이름을 들고
 * 와도 사람이 아무것도 안 해도 색이 생긴다 — 설정해야만 보이는 화면은 결국 설정되지
 * 않은 채로 쓰인다. 자동으로 고른 색이 마음에 안 들 때만 관리자에서 덮어쓴다.
 *
 * 해싱이라 **이름이 같으면 언제나 같은 색**이다. 카메라를 옮겨도, 새로고침해도,
 * 다른 사람 화면에서도 사람은 늘 같은 색이다. 무작위로 고르면 그것이 깨진다.
 */

/** 시안 팔레트에서 고른, 어두운 영상 위에서 서로 구별되는 색들.
 *  붉은 계열을 앞에 두지 않는다 — 첫 이름이 우연히 '위험' 색을 갖는 것을 피한다. */
const PALETTE = [
  "#e8703a", // 주황 (시안 강조색)
  "#4a9eda", // 파랑
  "#5db872", // 초록
  "#d4a017", // 노랑
  "#a978d8", // 보라
  "#3fb8ae", // 청록
  "#e0679b", // 분홍
  "#8a9a5b", // 올리브
];

/** 이름에서 인스턴스 번호를 뗀다.
 *
 * 플러그인이 `사람#2`, `위험구역 AMR#7` 처럼 객체마다 번호를 붙여 보내는 경우가 있다.
 * 그대로 해싱하면 같은 종류인데 번호마다 색이 달라져 화면이 알록달록해지기만 한다.
 * 종류로 묶어 색을 준다. */
export function colorKey(label: string): string {
  return (label || "").split("#")[0].trim();
}

function hash(text: string): number {
  // djb2. 암호용이 아니라 팔레트를 고르는 용도라 짧고 고르게 퍼지면 충분하다.
  let h = 5381;
  for (let i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/**
 * 박스 라벨 -> 선 색.
 *
 * `overrides` 는 관리자에서 적어 둔 '이름 -> #rrggbb' 표다. 번호를 뗀 이름으로 먼저
 * 찾고, 없으면 적힌 그대로도 찾아 본다 — 사람이 `사람#2` 라고 적었을 수도 있다.
 */
export function boxColor(label: string, overrides: Record<string, string> = {}): string {
  const key = colorKey(label);
  const hit = overrides[key] ?? overrides[label];
  if (hit) return hit;
  if (!key) return PALETTE[0];
  return PALETTE[hash(key) % PALETTE.length];
}

/** 라벨 글자색 — 선 색 위에 얹어도 읽히도록 흰색 계열로 고정한다.
 *  선 색을 그대로 쓰면 어두운 팔레트에서 글자가 영상에 묻힌다. */
export const LABEL_FILL = "#ffffff";

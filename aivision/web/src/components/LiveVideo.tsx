/**
 * MJPEG 라이브 뷰.
 *
 * 영상은 <img>(MJPEG) 로, 바운딩 박스는 그 위에 겹친 SVG 로 그린다. 서버가 프레임에
 * 박스를 구워 보내지 않는 이유:
 *   · 판정이 외부(카메라 엣지)에서 오면 영상 경로와 판정 경로가 애초에 분리돼 있다.
 *   · 나중에 전송 방식을 바꿔도(WebRTC 등) 오버레이 코드는 그대로 쓴다.
 *   · 한글 라벨을 OpenCV 로 그리면 깨진다. 브라우저에서 그리면 그럴 일이 없다.
 *
 * 박스 좌표는 0~1 정규화다. 다만 SVG 를 viewBox="0 0 1 1" 로 두면 안 된다 —
 * 두 가지가 조용히 깨진다.
 *   · 정사각 좌표계라 16:9 영상과 레터박스가 어긋나 박스가 엉뚱한 자리에 그려진다.
 *   · font-size 가 0.035 같은 소수가 되어 브라우저의 글자 배치가 무너진다(낱자가 흩어진다).
 * 그래서 **영상의 실제 픽셀 크기를 viewBox 로 쓴다.** <img> 가 실려야 알 수 있으므로
 * onLoad 에서 naturalWidth/Height 를 받아 둔다. 영상과 SVG 가 같은 비율·같은 정렬
 * (object-contain / xMidYMid meet)을 쓰므로 레터박스까지 정확히 겹친다.
 *
 * 영상은 object-contain 으로 맞춘다 — object-cover 로 자르면 박스와 영상이 어긋난다.
 *
 * 주의: MJPEG 은 연결을 계속 붙잡는다. 브라우저의 동시 연결 한도(HTTP/1.1 기준 6개) 때문에
 *   화면에 여러 개를 동시에 띄우면 다른 API 호출이 막힌다. active=false 면 <img> 를 아예
 *   내려 연결을 끊는다.
 */
import { useEffect, useState } from "react";

import { api } from "../lib/api";
import { LABEL_FILL, boxColor } from "../lib/boxcolor";
import { fmtClock } from "../lib/format";
import { useSettings } from "../lib/hooks";
import type { Box, Camera } from "../lib/types";
import { cx } from "./ui";

interface Props {
  camera: Camera;
  boxes?: Box[];
  active?: boolean;
  now?: Date;
  overlayTitle?: string;
  onClick?: () => void;
  className?: string;
  /** 부모 높이를 꽉 채운다(16:9 고정 대신). 종합 현황의 큰 화면처럼 옆 요소와 높이를 맞출 때. */
  fill?: boolean;
}

export function LiveVideo({
  camera,
  boxes = [],
  active = true,
  now,
  overlayTitle,
  onClick,
  className,
  fill = false,
}: Props) {
  // 색 덮어쓰기 표. 운영 설정은 staleTime 30초라 화면 여럿이 써도 한 번만 받아 온다.
  // 표가 비어 있어도(대개 비어 있다) 색은 나온다 — 이름을 해싱해 고르기 때문이다.
  const { data: settings } = useSettings();
  const colors = settings?.box_colors ?? {};

  const [failed, setFailed] = useState(false);
  const [nonce, setNonce] = useState(0);
  const [loaded, setLoaded] = useState(false);
  // 오버레이 좌표계. 영상이 실리기 전에는 16:9 로 가정한다(현장 카메라 기본값).
  const [size, setSize] = useState({ w: 1920, h: 1080 });

  // 카메라가 오프라인 → 정상으로 돌아오면 스트림을 다시 요청한다.
  useEffect(() => {
    if (camera.status === "normal") {
      setFailed(false);
      setNonce((n) => n + 1);
    }
  }, [camera.status]);

  // 카메라를 바꾸면 '아직 안 실렸다' 로 되돌린다.
  //
  // 이것이 없으면 loaded 가 이전 카메라에서 true 가 된 채 남는다. 새 영상의 첫 프레임이
  // 오기 전에 새 카메라의 박스가 이미 도착하므로, 지나간 화면 위에 다음 장면의 박스가
  // 얹혀 엉뚱한 곳을 가리키는 것처럼 보인다. 크게 띄운 화면은 볼 때 원본을 새로 여느라
  // 1~2초가 걸려서 그 틈이 눈에 띈다.
  useEffect(() => {
    setLoaded(false);
    setFailed(false);
  }, [camera.id]);

  const offline = camera.status !== "normal";
  const showStream = active && !offline && !failed;

  return (
    <div
      className={cx(
        "relative overflow-hidden rounded-lg border border-video-line bg-video-bg",
        fill && "h-full",
        onClick && "cursor-pointer",
        className,
      )}
      onClick={onClick}
      role={onClick ? "button" : undefined}
    >
      <div className={cx("relative w-full", fill ? "h-full" : "aspect-video")}>
        {showStream ? (
          <img
            // 카메라 번호를 열쇠에 넣어 <img> 를 새로 만든다. 같은 요소를 재사용하면
            // 이전 화면이 남은 채 주소만 바뀌어, 새 영상이 실릴 때까지 지난 장면이
            // 보인다. 앞의 MJPEG 연결도 이때 확실히 끊긴다 — 서버는 그 연결을 세어
            // 고화질 워커를 붙잡으므로, 안 끊으면 안 보는 카메라가 계속 원본을 문다.
            key={`${camera.id}:${nonce}`}
            src={`${api.streamUrl(camera.id)}?t=${nonce}`}
            alt={`${camera.name} 라이브 영상`}
            onLoad={(e) => {
              setLoaded(true);
              const img = e.currentTarget;
              if (img.naturalWidth > 0 && img.naturalHeight > 0) {
                setSize({ w: img.naturalWidth, h: img.naturalHeight });
              }
            }}
            onError={() => setFailed(true)}
            className="absolute inset-0 h-full w-full object-contain"
          />
        ) : null}

        {/* 박스 오버레이 — 영상과 같은 비율·정렬이라 레터박스까지 겹친다.
          *
          * **첫 프레임이 실린 뒤에만 그린다(loaded).** 박스는 WebSocket 으로 따로 오므로
          * 영상보다 먼저 도착한다. 먼저 그리면 아직 안 바뀐 화면 위에 다음 장면의 박스가
          * 얹혀, 탐지가 엉뚱한 곳을 짚은 것처럼 보인다. 화면과 박스는 같은 순간이어야 한다.
          * viewBox 도 첫 프레임의 naturalWidth/Height 로 정해지므로 그 전에는 좌표계 자체가
          * 이전 카메라의 것이다. */}
        {showStream && loaded && boxes.length > 0 && (
          <svg
            viewBox={`0 0 ${size.w} ${size.h}`}
            preserveAspectRatio="xMidYMid meet"
            className="pointer-events-none absolute inset-0 h-full w-full"
          >
            {boxes.map((b, i) => {
              const x = b.x1 * size.w;
              const y = b.y1 * size.h;
              const w = Math.max(0, (b.x2 - b.x1) * size.w);
              const h = Math.max(0, (b.y2 - b.y1) * size.h);
              const font = size.h * 0.028;
              const color = boxColor(b.label, colors);
              const label = b.score ? `${b.label} ${b.score.toFixed(2)}` : b.label;
              // 라벨이 화면 위로 잘리면 박스 안쪽으로 내린다.
              const labelY = y > font * 1.3 ? y - font * 0.4 : y + font * 1.1;
              return (
                <g key={i}>
                  <rect
                    x={x}
                    y={y}
                    width={w}
                    height={h}
                    fill="none"
                    stroke={color}
                    // non-scaling-stroke 이므로 이 값은 '화면 픽셀'이다.
                    // user unit 으로 착각해 소수를 넣으면 선이 사라진다.
                    strokeWidth={2}
                    vectorEffect="non-scaling-stroke"
                  />
                  {label && (
                    <text
                      x={x}
                      y={labelY}
                      fill={LABEL_FILL}
                      fontSize={font}
                      fontWeight={600}
                      stroke="rgba(0,0,0,.55)"
                      strokeWidth={3}
                      paintOrder="stroke"
                      vectorEffect="non-scaling-stroke"
                    >
                      {label}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        )}

        {/* 대기·실패 상태 */}
        {!showStream && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-[radial-gradient(110%_90%_at_45%_35%,#232a2b_0%,#121312_70%)]">
            <span className="text-[12.5px] text-[#8e8b82]">
              {offline ? "카메라 오프라인" : failed ? "영상 연결 실패" : "대기 중"}
            </span>
            {camera.last_error && (
              <span className="max-w-[80%] text-center text-[11px] text-[#6c6a64]">
                {camera.last_error}
              </span>
            )}
            {!offline && failed && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setFailed(false);
                  setNonce((n) => n + 1);
                }}
                className="rounded-md border border-[#3a3833] px-3 py-[5px] text-[12px] text-[#d8d3ca] transition-colors hover:bg-[#26241f]"
              >
                다시 시도
              </button>
            )}
          </div>
        )}

        {showStream && !loaded && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-[12.5px] text-[#8e8b82]">연결 중…</span>
          </div>
        )}

        {/* 상단 바 */}
        <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-center gap-2 bg-[linear-gradient(180deg,rgba(0,0,0,.55),transparent)] px-[13px] py-[11px]">
          <span className="text-[13px] font-semibold tracking-tight text-canvas">
            {overlayTitle ?? camera.name}
          </span>
          {showStream && loaded && (
            <span className="inline-flex items-center gap-[5px] text-[10.5px] font-semibold tracking-wider text-[#f1c2b3]">
              <b className="h-[7px] w-[7px] animate-blink rounded-full bg-[#e8703a]" />
              LIVE
            </span>
          )}
        </div>

        {/* 하단 바 */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex items-center justify-between bg-[linear-gradient(0deg,rgba(0,0,0,.6),transparent)] px-[13px] py-[10px] text-[11.5px] text-[#d8d3ca]">
          <span className="truncate">{camera.location}</span>
          {now && <span className="tnum shrink-0">{fmtClock(now)}</span>}
        </div>
      </div>
    </div>
  );
}

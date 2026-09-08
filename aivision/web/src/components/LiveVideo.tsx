/**
 * MJPEG 라이브 뷰.
 *
 * 영상은 <img>(MJPEG) 로, 바운딩 박스는 그 위에 겹친 SVG 로 그린다. 서버가 프레임에
 * 박스를 구워 보내지 않는 이유:
 *   · 판정이 외부(카메라 엣지)에서 오면 영상 경로와 판정 경로가 애초에 분리돼 있다.
 *   · 나중에 전송 방식을 바꿔도(WebRTC 등) 오버레이 코드는 그대로 쓴다.
 *   · 한글 라벨을 OpenCV 로 그리면 깨진다. 브라우저에서 그리면 그럴 일이 없다.
 *
 * 박스 좌표는 0~1 정규화라 SVG viewBox="0 0 1 1" 에 그대로 얹힌다. 영상은 object-contain
 * 으로 맞춰 잘라내지 않는다 — object-cover 로 자르면 박스와 영상이 어긋난다.
 *
 * 주의: MJPEG 은 연결을 계속 붙잡는다. 브라우저의 동시 연결 한도(HTTP/1.1 기준 6개) 때문에
 *   화면에 여러 개를 동시에 띄우면 다른 API 호출이 막힌다. active=false 면 <img> 를 아예
 *   내려 연결을 끊는다.
 */
import { useEffect, useState } from "react";

import { api } from "../lib/api";
import { fmtClock } from "../lib/format";
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
  const [failed, setFailed] = useState(false);
  const [nonce, setNonce] = useState(0);
  const [loaded, setLoaded] = useState(false);

  // 카메라가 오프라인 → 정상으로 돌아오면 스트림을 다시 요청한다.
  useEffect(() => {
    if (camera.status === "normal") {
      setFailed(false);
      setNonce((n) => n + 1);
    }
  }, [camera.status]);

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
            key={nonce}
            src={`${api.streamUrl(camera.id)}?t=${nonce}`}
            alt={`${camera.name} 라이브 영상`}
            onLoad={() => setLoaded(true)}
            onError={() => setFailed(true)}
            className="absolute inset-0 h-full w-full object-contain"
          />
        ) : null}

        {/* 박스 오버레이 — 영상과 같은 정규화 좌표계 */}
        {showStream && boxes.length > 0 && (
          <svg
            viewBox="0 0 1 1"
            preserveAspectRatio="xMidYMid meet"
            className="pointer-events-none absolute inset-0 h-full w-full"
          >
            {boxes.map((b, i) => (
              <g key={i}>
                <rect
                  x={b.x1}
                  y={b.y1}
                  width={Math.max(0, b.x2 - b.x1)}
                  height={Math.max(0, b.y2 - b.y1)}
                  fill="none"
                  stroke="#e8703a"
                  strokeWidth={0.004}
                  vectorEffect="non-scaling-stroke"
                />
                <text
                  x={b.x1}
                  y={Math.max(0.03, b.y1 - 0.012)}
                  fill="#f1c2b3"
                  fontSize={0.035}
                  fontWeight={600}
                >
                  {b.label} {b.score ? b.score.toFixed(2) : ""}
                </text>
              </g>
            ))}
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

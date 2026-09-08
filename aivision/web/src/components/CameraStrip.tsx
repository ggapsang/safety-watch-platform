/** 카메라 필름 스트립 — 세로로 쌓인 작은 미리보기.
 *
 * 미리보기는 MJPEG 스트림이 아니라 **주기적 스냅샷**을 쓴다.
 * MJPEG 은 연결을 계속 붙잡는데 브라우저의 동시 연결 한도가 HTTP/1.1 기준 6개다.
 * 카메라가 몇 대만 늘어도 스트립이 그 한도를 다 먹어 나머지 API 호출이 막힌다.
 * 큰 화면 하나만 MJPEG 을 쓰고, 스트립은 몇 초에 한 장씩 갈아 끼운다.
 */
import { useEffect, useState } from "react";

import { api } from "../lib/api";
import type { Camera } from "../lib/types";
import { Dot, cx } from "./ui";

const REFRESH_MS = 4000;

function Thumb({ camera, active }: { camera: Camera; active: boolean }) {
  const [nonce, setNonce] = useState(0);
  const [failed, setFailed] = useState(false);
  const offline = camera.status !== "normal";

  useEffect(() => {
    if (offline) return;
    // 화면 밖 탭에서는 브라우저가 타이머를 늦추므로 그대로 두어도 부담이 없다.
    const id = window.setInterval(() => setNonce((n) => n + 1), REFRESH_MS);
    return () => window.clearInterval(id);
  }, [offline]);

  useEffect(() => setFailed(false), [camera.status]);

  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-md bg-video-bg">
      {!offline && !failed ? (
        <img
          src={`${api.stillUrl(camera.id)}?t=${nonce}`}
          alt={`${camera.name} 미리보기`}
          onError={() => setFailed(true)}
          className="absolute inset-0 h-full w-full object-cover"
        />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center bg-[radial-gradient(110%_90%_at_45%_35%,#232a2b_0%,#121312_70%)] text-[11px] text-[#6c6a64]">
          {offline ? "오프라인" : "대기"}
        </div>
      )}
      {active && <div className="pointer-events-none absolute inset-0 ring-2 ring-inset ring-primary" />}
    </div>
  );
}

export function CameraStrip({
  cameras,
  selectedId,
  onSelect,
}: {
  cameras: Camera[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto pr-1">
      {cameras.map((cam) => {
        const active = cam.id === selectedId;
        return (
          <button
            key={cam.id}
            type="button"
            onClick={() => onSelect(cam.id)}
            className={cx(
              "shrink-0 rounded-lg border p-[6px] text-left transition-colors",
              active
                ? "border-primary bg-primary-soft/40"
                : "border-hairline bg-canvas hover:border-primary/40 hover:bg-surface-soft",
            )}
          >
            <Thumb camera={cam} active={active} />
            <div className="mt-[6px] flex items-center gap-[6px] px-[2px]">
              <Dot ok={cam.status === "normal"} />
              <span
                className={cx(
                  "truncate text-[11.5px]",
                  active ? "font-semibold text-ink" : "text-body",
                )}
              >
                {cam.location || cam.name}
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

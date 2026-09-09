/** 카메라 필름 스트립 — 세로로 쌓인 작은 미리보기.
 *
 * 미리보기는 MJPEG 스트림이 아니라 **주기적 스냅샷**을 쓴다.
 * MJPEG 은 연결을 계속 붙잡는데 브라우저의 동시 연결 한도가 HTTP/1.1 기준 6개다.
 * 카메라가 몇 대만 늘어도 스트립이 그 한도를 다 먹어 나머지 API 호출이 막힌다.
 * 큰 화면 하나만 MJPEG 을 쓰고, 스트립은 몇 초에 한 장씩 갈아 끼운다.
 *
 * 끌어서 순서를 바꿀 수 있다. 등록 순서가 곧 보고 싶은 순서인 경우는 드물기 때문이다
 * (게이트 → 야적장 → 출하구 처럼 동선대로 보고 싶어 한다). 순서는 서버에 저장하므로
 * 관제실 PC 가 여러 대여도 같게 보인다.
 *
 * 드래그 라이브러리를 쓰지 않은 이유: 세로 한 줄에 카메라 몇 대뿐이라 HTML5 기본
 * draggable 로 충분하다. 목록이 커지거나 다중 선택이 필요해지면 그때 라이브러리를 붙인다.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

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
          // 끌 때 브라우저가 이미지 자체를 끌고 가면 순서 바꾸기와 뒤엉킨다.
          draggable={false}
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
  const qc = useQueryClient();
  // 끄는 동안에는 손끝을 따라 즉시 자리가 바뀌어야 한다. 서버 응답을 기다리면 뚝뚝 끊긴다.
  const [order, setOrder] = useState<Camera[] | null>(null);
  const [dragId, setDragId] = useState<number | null>(null);
  const moved = useRef(false);

  const items = order ?? cameras;

  // 끌고 있지 않을 때만 서버 목록을 따른다. 끄는 도중에 목록이 갱신되면 자리가 튄다.
  useEffect(() => {
    if (dragId === null) setOrder(null);
  }, [cameras, dragId]);

  const save = useMutation({
    mutationFn: (ids: number[]) => api.reorderCameras(ids),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cameras"] }),
    // 실패하면 서버 순서로 되돌린다. 화면만 바뀌고 저장은 안 된 상태로 두지 않는다.
    onError: () => setOrder(null),
  });

  function onDragStart(id: number) {
    setDragId(id);
    moved.current = false;
  }

  function onDragEnter(overId: number) {
    if (dragId === null || dragId === overId) return;
    const list = [...items];
    const from = list.findIndex((c) => c.id === dragId);
    const to = list.findIndex((c) => c.id === overId);
    if (from < 0 || to < 0) return;
    list.splice(to, 0, ...list.splice(from, 1));
    moved.current = true;
    setOrder(list);
  }

  function onDragEnd() {
    const list = order;
    setDragId(null);
    if (!moved.current || !list) {
      setOrder(null);
      return;
    }
    save.mutate(list.map((c) => c.id));
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto pr-1">
      {items.map((cam) => {
        const active = cam.id === selectedId;
        const dragging = cam.id === dragId;
        return (
          <button
            key={cam.id}
            type="button"
            draggable
            onDragStart={() => onDragStart(cam.id)}
            onDragEnter={() => onDragEnter(cam.id)}
            onDragOver={(e) => e.preventDefault()}   // 이게 없으면 드롭이 허용되지 않는다
            onDragEnd={onDragEnd}
            onClick={() => onSelect(cam.id)}
            title="끌어서 순서를 바꿀 수 있습니다"
            className={cx(
              "shrink-0 cursor-grab rounded-lg border p-[6px] text-left transition-colors active:cursor-grabbing",
              active
                ? "border-primary bg-primary-soft/40"
                : "border-hairline bg-canvas hover:border-primary/40 hover:bg-surface-soft",
              dragging && "opacity-40",
              save.isPending && "pointer-events-none",
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

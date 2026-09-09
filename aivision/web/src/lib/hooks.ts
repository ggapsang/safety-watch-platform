/** 공용 훅 — 데이터 조회와 서버 푸시 구독. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api, type EventQuery } from "./api";
import { subscribe, subscribeState, type LiveState } from "./live";
import type { Box, Bucket, PushMessage } from "./types";

/* ────────────────────────────────────────────────── 데이터 조회 */

export const useCameras = () =>
  useQuery({ queryKey: ["cameras"], queryFn: api.cameras, staleTime: 5_000 });

export const useSummary = () =>
  useQuery({ queryKey: ["summary"], queryFn: api.summary, refetchInterval: 15_000 });

export const useEvents = (query: EventQuery) =>
  useQuery({ queryKey: ["events", query], queryFn: () => api.events(query) });

export const useStats = (query: { bucket: Bucket } & Omit<EventQuery, "limit" | "offset" | "q">) =>
  useQuery({ queryKey: ["stats", query], queryFn: () => api.stats(query) });

export const useSolutions = () =>
  useQuery({ queryKey: ["solutions"], queryFn: api.solutions, staleTime: 60_000 });

export const useSettings = () =>
  useQuery({ queryKey: ["settings"], queryFn: api.settings, staleTime: 30_000 });

export const useSystem = () =>
  useQuery({ queryKey: ["system"], queryFn: api.system, refetchInterval: 5_000 });

/** 분석 모듈 목록. 사이드바가 '화면을 가진 모듈' 을 탭으로 만들 때 쓴다.
 *  모듈은 재시작하며 붙었다 떨어지므로 주기적으로 다시 본다. */
export const useModules = () =>
  useQuery({ queryKey: ["modules"], queryFn: api.modules, refetchInterval: 15_000 });

export type { LiveState };

export const useBindings = () =>
  useQuery({ queryKey: ["bindings"], queryFn: api.bindings });

/* ────────────────────────────────────────────────── 서버 푸시 */

/**
 * 서버 푸시 구독.
 *
 * 연결 자체는 lib/live.ts 가 앱 전체에 하나만 유지한다. 여기서는 구독만 건다 —
 * 훅마다 WebSocket 을 열면 화면을 옮길 때마다 연결이 늘어난다.
 *
 * 새 이벤트가 오면 관련 쿼리를 무효화해 화면이 스스로 최신화되게 한다.
 * 폴링을 쓰지 않는 이유: 관제 화면에서 몇 초의 지연은 그대로 대응 지연이 된다.
 */
export function useLiveFeed(onMessage?: (msg: PushMessage) => void): LiveState {
  const qc = useQueryClient();
  const [state, setState] = useState<LiveState>("connecting");
  const handler = useRef(onMessage);
  handler.current = onMessage;

  useEffect(() => subscribeState(setState), []);

  useEffect(
    () =>
      subscribe((msg) => {
        if (msg.kind === "event") {
          qc.invalidateQueries({ queryKey: ["events"] });
          qc.invalidateQueries({ queryKey: ["summary"] });
          qc.invalidateQueries({ queryKey: ["stats"] });
          qc.invalidateQueries({ queryKey: ["cameras"] });
        } else if (msg.kind === "camera-status" || msg.kind === "cameras-changed") {
          qc.invalidateQueries({ queryKey: ["cameras"] });
          qc.invalidateQueries({ queryKey: ["summary"] });
        }
        handler.current?.(msg);
      }),
    [qc],
  );

  return state;
}

/**
 * 카메라별 라이브 오버레이 박스.
 *
 * 이벤트와 성격이 다르다 — 초당 여러 번 흐르고 영속하지 않는다. 그래서 react-query 를
 * 거치지 않고 곧장 상태로 받는다. 캐시에 넣으면 매 프레임 무효화가 돌아 화면이 멎는다.
 *
 * 일정 시간 새 박스가 없으면 지운다. 탐지가 끝났는데 마지막 박스가 화면에 남아 있으면 안 된다.
 */
export function useLiveBoxes(ttlMs = 2000): Record<number, Box[]> {
  const [boxes, setBoxes] = useState<Record<number, Box[]>>({});
  const seen = useRef<Record<number, number>>({});

  useEffect(
    () =>
      subscribe((msg) => {
        if (msg.kind !== "live-boxes") return;
        seen.current[msg.data.camera_id] = Date.now();
        setBoxes((prev) => ({ ...prev, [msg.data.camera_id]: msg.data.boxes }));
      }),
    [],
  );

  useEffect(() => {
    const id = window.setInterval(() => {
      const now = Date.now();
      const stale = Object.entries(seen.current)
        .filter(([, t]) => now - t > ttlMs)
        .map(([k]) => Number(k));
      if (stale.length === 0) return;
      stale.forEach((k) => delete seen.current[k]);
      setBoxes((prev) => {
        const next = { ...prev };
        stale.forEach((k) => delete next[k]);
        return next;
      });
    }, 500);
    return () => window.clearInterval(id);
  }, [ttlMs]);

  return boxes;
}

/** 1초마다 갱신되는 현재 시각(헤더 시계). */
export function useClock(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

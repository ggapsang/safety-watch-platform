/** 서버 푸시 연결 — 앱 전체가 **하나만** 쓴다.
 *
 * 훅마다 WebSocket 을 열면 화면을 옮길 때마다 연결이 늘어난다(실제로 대시보드와 카메라
 * 화면이 각각 열어 세 개가 되는 일이 있었다). 그래서 모듈 수준에서 하나를 만들어 두고
 * 구독자만 붙였다 뗀다. 구독자가 0 이 되면 닫는다.
 *
 * 끊기면 지수 백오프로 다시 붙는다. 서버를 재시작해도 화면이 알아서 복구된다.
 */
import type { PushMessage } from "./types";

export type LiveState = "connecting" | "open" | "closed";

type Listener = (msg: PushMessage) => void;
type StateListener = (s: LiveState) => void;

const listeners = new Set<Listener>();
const stateListeners = new Set<StateListener>();

let socket: WebSocket | null = null;
let state: LiveState = "closed";
let retry = 0;
let timer: number | undefined;

function setState(next: LiveState): void {
  if (state === next) return;
  state = next;
  stateListeners.forEach((fn) => fn(next));
}

function connect(): void {
  if (socket) return;
  setState("connecting");
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/ws`);
  socket = ws;

  ws.onopen = () => {
    retry = 0;
    setState("open");
  };
  ws.onmessage = (ev) => {
    let msg: PushMessage;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (msg.kind === "ping") return;
    listeners.forEach((fn) => fn(msg));
  };
  ws.onclose = () => {
    socket = null;
    setState("closed");
    if (listeners.size === 0 && stateListeners.size === 0) return;
    const delay = Math.min(1000 * 2 ** retry++, 15_000);
    timer = window.setTimeout(connect, delay);
  };
  ws.onerror = () => ws.close();
}

function maybeClose(): void {
  if (listeners.size > 0 || stateListeners.size > 0) return;
  if (timer) window.clearTimeout(timer);
  timer = undefined;
  socket?.close();
  socket = null;
}

export function subscribe(fn: Listener): () => void {
  listeners.add(fn);
  connect();
  return () => {
    listeners.delete(fn);
    maybeClose();
  };
}

export function subscribeState(fn: StateListener): () => void {
  stateListeners.add(fn);
  fn(state);
  connect();
  return () => {
    stateListeners.delete(fn);
    maybeClose();
  };
}

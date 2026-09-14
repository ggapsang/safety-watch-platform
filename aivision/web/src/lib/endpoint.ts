/** 플러그인 화면 주소를 **브라우저가 실제로 닿을 수 있는 주소**로 옮긴다.
 *
 * **왜 필요한가.** 플러그인은 등록할 때 자기 화면 주소(`endpoint`)를 준다. 대부분
 * `http://localhost:11990` 처럼 적는데, 이 값은 플랫폼을 서버 PC 에서 열 때만 맞다.
 * 다른 PC 에서 `http://192.168.9.54:11880` 으로 들어오면 그 `localhost` 는 **보는 사람의
 * PC** 를 가리키게 되고, 거기엔 아무것도 없으니 '연결을 거부했습니다' 가 뜬다.
 *
 * `.env` 마다 서버 IP 를 적게 할 수도 있지만 그것은 반쪽이다 — 서버 IP 가 바뀌면 다시
 * 깨지고, 플러그인이 늘 때마다 같은 실수가 반복된다. 실제로 반복됐다.
 *
 * **판단 근거.** 플러그인이 `localhost` 라고 적었다는 것은 '플랫폼과 같은 기계에 있다'
 * 는 뜻이다. 그 기계는 지금 브라우저가 대시보드를 받아 온 바로 그 호스트다. 그러니
 * 호스트만 갈아 끼우고 포트·경로는 그대로 둔다.
 *
 * 다른 PC 에서 도는 플러그인은 애초에 `localhost` 가 아닌 주소를 적으므로 건드리지 않는다.
 *
 * 이것은 특정 플러그인을 아는 코드가 아니다. 주소 한 줄을 옮길 뿐이라 매니페스토 2번과
 * 무관하다.
 */

const LOOPBACK = new Set(["localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"]);

export function reachableEndpoint(endpoint: string): string {
  if (!endpoint) return endpoint;
  try {
    const url = new URL(endpoint);
    if (!LOOPBACK.has(url.hostname)) return endpoint;
    url.hostname = window.location.hostname;
    // 프로토콜은 바꾸지 않는다. 플랫폼이 https 인데 플러그인이 http 면 브라우저가
    // 어차피 막는데(mixed content), 여기서 https 로 바꿔 주면 '연결은 됐는데 인증서가
    // 없다' 는 더 알기 어려운 오류가 된다. 있는 그대로 두고 실패하게 둔다.
    return url.toString().replace(/\/$/, endpoint.endsWith("/") ? "/" : "");
  } catch {
    // URL 로 못 읽는 값이면 손대지 않는다. 플러그인이 이상하게 적은 것이고,
    // 그것을 우리가 고쳐 주는 척하면 원인이 더 안 보인다.
    return endpoint;
  }
}

/** 주소가 우리 손에 옮겨졌는지 — 화면이 그 사실을 알려 줄 때 쓴다. */
export function wasRewritten(endpoint: string): boolean {
  return reachableEndpoint(endpoint) !== endpoint;
}

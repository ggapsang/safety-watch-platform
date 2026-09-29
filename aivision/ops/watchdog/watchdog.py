"""ops-watchdog — 스택을 지켜보고, 기록하고, 필요하면 되살린다.

**왜 있는가.** 이 시스템은 사람이 자는 동안에도 돌아야 한다. 그런데 지금까지 문제는 늘
'느려졌다' 는 말로 발견됐고, 그때 한 장면을 찍어 보는 것 말고는 언제부터·무엇과 함께
나빠졌는지 알 길이 없었다. 메모리가 사흘에 1GB 씩 오르는 동안 그것을 적어 둔 곳이 없었다.

그래서 둘을 한다.
  1. **기록한다.** 30초마다 컨테이너별 메모리·CPU·스레드, 가상머신 여유 메모리, 카메라
     연결 수, 추론 진척을 한 줄씩 남긴다(날짜별 JSONL). 다음에 무언가 나빠지면 그 기울기가
     이미 파일에 있다.
  2. **되살린다.** 정해 둔 선을 넘으면 해당 컨테이너를 재기동하고, 그 사실과 이유를 따로
     남긴다(actions.jsonl).

**재기동은 치료가 아니다.** 원인을 고치는 것은 코드의 일이고, 이것은 원인을 고칠 때까지
화면이 죽어 있지 않게 하는 안전망이다. 그래서 스스로를 묶어 둔다.
  · 선을 **연달아** 넘어야 움직인다(순간 튐으로 재기동하지 않는다).
  · 재기동한 컨테이너는 한동안 건드리지 않는다(쿨다운 — 뜨는 동안 메모리가 오르는 것을
    또 넘었다고 보면 안 된다).
  · 6시간에 정해진 횟수를 넘기면 **손을 뗀다**(서킷 브레이커). 재기동으로 안 낫는 문제를
    재기동으로 계속 두드리면 로그만 채우고 진짜 원인을 가린다 — 그때는 사람이 봐야 한다.
  · 카메라 쪽 문제(카메라·네트워크가 죽음)는 재기동으로 고쳐지지 않는다. 미디어 서버가
    카메라를 받고 있는지(ready) 먼저 보고, 받고 있는데도 앱이 못 받을 때만 앱을 재기동한다.

이 모듈은 도커 소켓을 붙여 컨테이너를 재기동할 수 있다. 즉 호스트의 도커 전체를 쥔다.
단일 호스트 현장이라 그렇게 두지만, 여러 사람이 쓰는 호스트라면 다시 봐야 한다.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import docker

log = logging.getLogger("watchdog")


# ────────────────────────────────────────────────────────────── 설정

def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _num(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        log.warning("%s 를 숫자로 읽지 못했습니다 — %s 로 진행합니다", name, default)
        return default


PROJECT = _env("COMPOSE_PROJECT", "aivision")
INTERVAL = _num("INTERVAL_SEC", 30)
LOG_DIR = Path(_env("LOG_DIR", "/ops"))
KEEP_DAYS = int(_num("KEEP_DAYS", 30))
DRY_RUN = _env("DRY_RUN", "false").lower() in ("1", "true", "yes", "on")

APP_URL = _env("APP_URL", "http://base-app:8000").rstrip("/")
YOLO_URL = _env("YOLO_URL", "http://mod-yolo:8000").rstrip("/")
MEDIA_API = _env("MEDIA_API", "http://base-media:9997").rstrip("/")

# 선. '평소의 몇 배' 가 아니라 '이만큼이면 이상하다' 는 절대값이다. 평소 값이 바뀌면(모델을
# 바꾸면 객체감지 메모리가 달라진다) 여기도 같이 봐야 한다 — 기록 파일이 그 근거가 된다.
MEM_SOFT_MB = {
    "base-app": _num("BASE_APP_MEM_SOFT_MB", 2000),
    "mod-yolo": _num("YOLO_MEM_SOFT_MB", 6000),
    "mod-camera-meta": _num("CAMERA_META_MEM_SOFT_MB", 800),
    "mod-collision": _num("COLLISION_MEM_SOFT_MB", 400),
}
VM_MIN_AVAILABLE_MB = _num("VM_MIN_AVAILABLE_MB", 1500)

# 몇 번 연달아 넘어야 움직이나. INTERVAL 30초 기준.
STRIKES_MEM = int(_num("STRIKES_MEM", 4))            # 2분
STRIKES_UNHEALTHY = int(_num("STRIKES_UNHEALTHY", 4))  # 2분
STRIKES_NO_VIDEO = int(_num("STRIKES_NO_VIDEO", 6))    # 3분
STRIKES_STALL = int(_num("STRIKES_STALL", 8))          # 4분
STRIKES_VM = int(_num("STRIKES_VM", 4))                # 2분

COOLDOWN_SEC = _num("COOLDOWN_SEC", 900)               # 재기동 뒤 15분은 건드리지 않는다
BREAKER_WINDOW_SEC = _num("BREAKER_WINDOW_SEC", 6 * 3600)
BREAKER_MAX = int(_num("BREAKER_MAX", 4))              # 6시간에 4번 넘으면 손을 뗀다

# 재기동해도 되는 것. DB 와 브로커는 넣지 않는다 — 재기동이 곧 데이터·세션을 흔들고,
# 이 둘이 문제였던 적도 없다. 넣는 것은 실제로 새거나 멈춘 적이 있는 것들이다.
RESTARTABLE = {"base-app", "mod-yolo", "mod-camera-meta", "mod-collision", "base-media"}


# ────────────────────────────────────────────────────────────── 기록

class Recorder:
    """날짜별 JSONL. 한 줄 = 한 시점. 오래된 파일은 KEEP_DAYS 가 지나면 지운다."""

    def __init__(self, root: Path) -> None:
        self.metrics = root / "metrics"
        self.metrics.mkdir(parents=True, exist_ok=True)
        self.actions = root / "actions.jsonl"
        self.latest = root / "latest.json"
        self._last_prune = 0.0

    def sample(self, row: dict) -> None:
        day = datetime.now().strftime("%Y-%m-%d")
        with (self.metrics / f"{day}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        # 지금 상태 한 장. '지금 괜찮나' 를 볼 때 하루치 파일을 뒤질 필요가 없게.
        tmp = self.latest.with_suffix(".tmp")
        tmp.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.latest)
        if time.time() - self._last_prune > 3600:
            self._last_prune = time.time()
            self._prune()

    def action(self, **fields) -> None:
        fields = {"ts": datetime.now().isoformat(timespec="seconds"), **fields}
        with self.actions.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fields, ensure_ascii=False) + "\n")
        log.warning("조치: %s", json.dumps(fields, ensure_ascii=False))

    def _prune(self) -> None:
        cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
        for p in self.metrics.glob("*.jsonl"):
            try:
                if datetime.strptime(p.stem, "%Y-%m-%d") < cutoff:
                    p.unlink()
                    log.info("오래된 기록 삭제: %s", p.name)
            except ValueError:
                continue


# ────────────────────────────────────────────────────────────── 수집

def _http_json(url: str, timeout: float = 5.0):
    """못 받으면 None. 감시가 감시 대상 때문에 죽으면 안 된다."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:        # noqa: S310
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _vm_meminfo() -> dict:
    """가상머신(도커가 도는 WSL) 전체의 메모리. 컨테이너 안에서 보이는 /proc/meminfo 가 그것이다."""
    out = {}
    try:
        for line in open("/proc/meminfo", encoding="ascii"):
            k, v = line.split(":", 1)
            if k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                out[k] = int(v.split()[0]) // 1024
    except OSError:
        pass
    return out


def _container_stats(c) -> dict:
    """docker stats 와 같은 셈법. 메모리는 캐시(inactive_file)를 뺀다."""
    try:
        s = c.stats(stream=False)
    except Exception as exc:                                          # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"[:120]}
    mem = s.get("memory_stats") or {}
    stats = mem.get("stats") or {}
    usage = mem.get("usage", 0) - stats.get("inactive_file", stats.get("total_inactive_file", 0))
    cpu, pre = s.get("cpu_stats") or {}, s.get("precpu_stats") or {}
    cpu_delta = (cpu.get("cpu_usage", {}).get("total_usage", 0)
                 - pre.get("cpu_usage", {}).get("total_usage", 0))
    sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    online = cpu.get("online_cpus") or len(cpu.get("cpu_usage", {}).get("percpu_usage") or []) or 1
    cpu_pct = (cpu_delta / sys_delta * online * 100) if sys_delta > 0 else 0.0
    return {
        "mem_mb": round(max(usage, 0) / 1024 / 1024, 1),
        "mem_limit_mb": round(mem.get("limit", 0) / 1024 / 1024),
        "cpu_pct": round(cpu_pct, 1),
        "pids": (s.get("pids_stats") or {}).get("current"),
    }


class Collector:
    def __init__(self) -> None:
        self.docker = docker.from_env(timeout=30)
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="stats")

    def containers(self) -> dict:
        found = self.docker.containers.list(
            all=True, filters={"label": f"com.docker.compose.project={PROJECT}"})
        return {c.labels.get("com.docker.compose.service", c.name): c for c in found}

    def collect(self) -> tuple[dict, dict]:
        cons = self.containers()
        futures = {svc: self.pool.submit(_container_stats, c)
                   for svc, c in cons.items() if c.status == "running"}
        row: dict = {"ts": datetime.now().isoformat(timespec="seconds"),
                     "vm": _vm_meminfo(), "containers": {}}
        for svc, c in sorted(cons.items()):
            attrs = c.attrs.get("State", {})
            entry = {"status": c.status,
                     "health": (attrs.get("Health") or {}).get("Status", ""),
                     "restarts": c.attrs.get("RestartCount", 0),
                     "oom_killed": attrs.get("OOMKilled", False)}
            if svc in futures:
                entry.update(futures[svc].result())
            row["containers"][svc] = entry

        # 앱 안쪽. 컨테이너 숫자만으로는 '살아 있는데 일을 안 한다' 를 못 가른다.
        row["app"] = _http_json(f"{APP_URL}/api/system/process")
        row["app_health"] = _http_json(f"{APP_URL}/api/health") is not None
        yolo = _http_json(f"{YOLO_URL}/api/state")
        if yolo:
            inf = yolo.get("inference") or {}
            row["yolo"] = {"mode": (yolo.get("module") or {}).get("mode"),
                           "cameras": len(inf.get("cameras") or []),
                           "frames": inf.get("frames"), "published": inf.get("published"),
                           "reconnects": inf.get("reconnects"),
                           "errors": len(inf.get("errors") or {}),
                           "memory": inf.get("memory")}
        paths = _http_json(f"{MEDIA_API}/v3/paths/list?itemsPerPage=200")
        if paths is not None:
            items = paths.get("items") or []
            row["media"] = {"paths": len(items),
                            "ready": sum(1 for p in items if p.get("ready")),
                            "readers": sum(len(p.get("readers") or []) for p in items)}
        else:
            row["media"] = None
        return row, cons


# ────────────────────────────────────────────────────────────── 판단

class Judge:
    """연달아 넘은 횟수를 세고, 쿨다운·서킷 브레이커를 지키며 재기동을 결정한다."""

    def __init__(self, recorder: Recorder) -> None:
        self.rec = recorder
        self.strikes: dict[tuple[str, str], int] = defaultdict(int)
        self.cooldown_until: dict[str, float] = {}
        self.history: dict[str, deque] = defaultdict(deque)
        self.breaker_open: set[str] = set()
        self.last_yolo_frames: int | None = None

    # 한 규칙이 이번 바퀴에 '넘었나' 를 받아 연속 횟수를 갱신한다.
    def _hit(self, svc: str, rule: str, over: bool, need: int) -> bool:
        key = (svc, rule)
        self.strikes[key] = self.strikes[key] + 1 if over else 0
        return self.strikes[key] >= need

    def evaluate(self, row: dict) -> list[tuple[str, str, dict]]:
        """(서비스, 규칙, 근거) 목록. 재기동할 것들이다."""
        want: list[tuple[str, str, dict]] = []
        cons = row.get("containers", {})

        # 1. 컨테이너 메모리
        for svc, soft in MEM_SOFT_MB.items():
            mem = (cons.get(svc) or {}).get("mem_mb")
            if mem is not None and self._hit(svc, "mem", mem > soft, STRIKES_MEM):
                want.append((svc, "mem", {"mem_mb": mem, "soft_mb": soft}))

        # 2. 앱이 응답하지 않는다
        if self._hit("base-app", "unhealthy", not row.get("app_health"), STRIKES_UNHEALTHY):
            want.append(("base-app", "unhealthy", {}))

        # 3. 카메라는 살아 있는데(미디어 서버가 받고 있다) 앱이 하나도 못 받는다.
        #    카메라·네트워크가 죽은 것이면 media.ready 가 0 이라 여기 안 걸린다 —
        #    그것은 재기동으로 고쳐지지 않는다.
        app, media = row.get("app") or {}, row.get("media") or {}
        streams = app.get("streams") or {}
        no_video = (streams.get("total", 0) > 0 and streams.get("connected", 0) == 0
                    and media.get("ready", 0) > 0)
        if self._hit("base-app", "no_video", no_video, STRIKES_NO_VIDEO):
            want.append(("base-app", "no_video",
                         {"streams": streams.get("total"), "media_ready": media.get("ready")}))

        # 4. 객체감지가 추론 중이라는데 프레임이 안 는다
        yolo = row.get("yolo") or {}
        frames = yolo.get("frames")
        stalled = (yolo.get("mode") == "inference" and yolo.get("cameras", 0) > 0
                   and frames is not None and self.last_yolo_frames is not None
                   and frames <= self.last_yolo_frames and media.get("ready", 0) > 0)
        if frames is not None:
            self.last_yolo_frames = frames
        if self._hit("mod-yolo", "stall", stalled, STRIKES_STALL):
            want.append(("mod-yolo", "stall", {"frames": frames}))

        # 5. 미디어 서버 API 가 안 보인다
        if self._hit("base-media", "unreachable", row.get("media") is None, STRIKES_UNHEALTHY):
            want.append(("base-media", "unreachable", {}))

        # 6. 가상머신 전체가 메모리에 쫓긴다 — 선에 대면 가장 많이 먹은 쪽을 내린다.
        #    이것이 호스트를 조이면 가상머신이 몇 초씩 멈추고, 그때 카메라가 한꺼번에
        #    끊긴다(실제로 같은 초에 워커 7개가 타임아웃났다).
        avail = (row.get("vm") or {}).get("MemAvailable")
        if avail is not None and self._hit("vm", "low_mem", avail < VM_MIN_AVAILABLE_MB, STRIKES_VM):
            ratio = {s: (cons.get(s) or {}).get("mem_mb", 0) / MEM_SOFT_MB[s] for s in MEM_SOFT_MB}
            worst = max(ratio, key=ratio.get)
            want.append((worst, "vm_low_mem",
                         {"vm_available_mb": avail, "chosen_by_ratio": round(ratio[worst], 2)}))
        return want

    def allowed(self, svc: str, rule: str, why: dict) -> bool:
        now = time.time()
        if svc not in RESTARTABLE:
            return False
        if now < self.cooldown_until.get(svc, 0):
            return False                       # 방금 재기동했다 — 뜨는 중이다
        hist = self.history[svc]
        while hist and now - hist[0] > BREAKER_WINDOW_SEC:
            hist.popleft()
        if len(hist) >= BREAKER_MAX:
            if svc not in self.breaker_open:
                self.breaker_open.add(svc)
                self.rec.action(service=svc, rule=rule, action="breaker_open",
                                note=f"{BREAKER_WINDOW_SEC / 3600:.0f}시간에 {BREAKER_MAX}번 "
                                     "재기동했는데도 계속 넘습니다. 재기동으로는 낫지 않는 문제라 "
                                     "자동 복구를 멈춥니다 — 사람이 봐야 합니다.", **why)
            return False
        self.breaker_open.discard(svc)
        return True

    def done(self, svc: str) -> None:
        now = time.time()
        self.history[svc].append(now)
        self.cooldown_until[svc] = now + COOLDOWN_SEC
        # 재기동한 서비스의 연속 횟수는 새로 센다. 뜨는 동안의 값으로 또 판단하지 않게.
        for key in list(self.strikes):
            if key[0] == svc:
                self.strikes[key] = 0
        if svc == "mod-yolo":
            self.last_yolo_frames = None


# ────────────────────────────────────────────────────────────── 본체

_stop = threading.Event()


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    signal.signal(signal.SIGINT, lambda *_: _stop.set())

    rec = Recorder(LOG_DIR)
    judge = Judge(rec)
    col = None
    log.info("감시 시작 — %.0f초마다 · 기록 %s · %s", INTERVAL, LOG_DIR,
             "DRY_RUN(재기동하지 않고 기록만)" if DRY_RUN else "자동 재기동 켜짐")
    rec.action(action="watchdog_start", interval_sec=INTERVAL, dry_run=DRY_RUN,
               mem_soft_mb=MEM_SOFT_MB, vm_min_available_mb=VM_MIN_AVAILABLE_MB)

    while not _stop.is_set():
        t0 = time.monotonic()
        try:
            if col is None:
                col = Collector()
            row, cons = col.collect()
            rec.sample(row)
            for svc, rule, why in judge.evaluate(row):
                if not judge.allowed(svc, rule, why):
                    continue
                c = cons.get(svc)
                if c is None:
                    continue
                if DRY_RUN:
                    rec.action(service=svc, rule=rule, action="would_restart", **why)
                else:
                    rec.action(service=svc, rule=rule, action="restart", **why)
                    try:
                        c.restart(timeout=20)
                    except Exception as exc:                          # noqa: BLE001
                        rec.action(service=svc, rule=rule, action="restart_failed",
                                   error=f"{type(exc).__name__}: {exc}"[:200])
                judge.done(svc)
        except Exception:                                             # noqa: BLE001
            # 감시가 죽으면 아무도 모른다. 무슨 일이 있어도 다음 바퀴로 간다.
            log.exception("감시 한 바퀴 실패 — 계속합니다")
            col = None                                                # 도커 연결부터 다시
        _stop.wait(max(1.0, INTERVAL - (time.monotonic() - t0)))
    log.info("감시 종료")
    return 0


if __name__ == "__main__":
    sys.exit(main())

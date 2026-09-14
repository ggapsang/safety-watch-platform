"""모듈 수명주기 — 등록 · 일감 · 구독 · 판정 스레드 · 생존 신고.

SDK 의 `Runner` 를 쓰지 않는다. 그것은 `Source` — '이 RTSP 를 열어 박스를 내놓아라' —
를 전제로 카메라마다 스레드를 띄우는 물건인데, 이 모듈은 **영상을 열지 않는다**.
입력은 브로커에서 오고, 카메라 수만큼 스레드를 띄울 이유도 없다. 그래서 계약의 네 가지
일(등록·일감·발행·heartbeat)을 여기서 직접 지킨다. SDK 를 쓰든 안 쓰든 계약은 같다 —
SDK 는 편의 구현일 뿐 특권 통로가 아니다(계약 서두).

스레드는 둘이다.
  · 배관 스레드   등록 · 20초 일감 폴링 · 30초 heartbeat  (이 클래스의 `run()`)
  · 판정 스레드   `tick_hz` 로 모든 카메라 파이프라인을 돌린다

카메라마다 스레드를 두지 않는 이유: 규칙 판정은 싸다(트랙 몇 개의 행렬 연산). 대신
카메라 하나에서 난 예외가 다른 카메라를 멈추지 않도록 틱마다 따로 감싼다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

import config as config_module
import settings as settings_module
from _sdk import Frame, LiveSubscriber, Platform, Publisher, WorkItem
from pipeline import CameraPipeline, Judgement

log = logging.getLogger(__name__)

DESCRIPTION = "AMR 과 사람의 접근·충돌을 본다 (남이 낸 박스를 받아 판정한다)"
JUDGEMENT_KEEP = 200


class Service:
    def __init__(self, cfg: config_module.Config) -> None:
        self.cfg = cfg
        self.platform = Platform(cfg.platform_url, cfg.module_id)
        self.publisher = Publisher(cfg.mqtt_host, cfg.mqtt_port, cfg.module_id)
        self.subscriber = LiveSubscriber(cfg.mqtt_host, cfg.mqtt_port, cfg.module_id)
        self.subscriber.on_boxes(self._on_frame)

        self.settings = self._load_settings()
        self.pipelines: dict[int, CameraPipeline] = {}
        self.work: list[WorkItem] = []
        self.judgements: deque[Judgement] = deque(maxlen=JUDGEMENT_KEEP)

        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self._judge_thread: threading.Thread | None = None
        self._sub_started = False
        self._registered: list[str] | None = None
        self.platform_seen = False
        self.last_error = ""
        self.foreign = 0                 # 담당이 아닌 카메라라 버린 프레임 수

    def _load_settings(self) -> settings_module.Settings:
        """설정 파일을 읽고, 아직 파일에 없는 것은 지금 도는 값으로 채운다.

        화면에서 무엇 하나를 저장하면 파일이 통째로 쓰인다. 채워 두지 않으면 라벨만
        저장했는데 파일에는 빈 항목 코드가 적히고, 다음 기동에서 그 빈 값이 사람의
        결정으로 읽혀 이벤트가 조용히 끊긴다.
        """
        s = settings_module.load(Path(self.cfg.config_dir))
        s.adopt(amr_labels=self.cfg.amr_labels, person_labels=self.cfg.person_labels,
                risk_item=self.cfg.risk_item, collision_item=self.cfg.collision_item,
                publish_live=self.cfg.publish_live, overlay_zones=self.cfg.overlay_zones)
        return s

    # ── 계약 1: 등록 ────────────────────────────────────────────────

    def register(self) -> bool:
        """같은 id 재호출은 갱신이라 몇 번을 불러도 안전하다(계약 1.1).

        항목 연결이 바뀌면 `capabilities` 도 바뀌므로 다시 부른다.
        """
        caps = self.cfg.capabilities
        ok = self.platform.register(self.cfg.module_name, caps, kind="sidecar",
                                    description=DESCRIPTION,
                                    endpoint=self.cfg.public_url)
        if ok:
            if self._registered != caps:
                log.info("등록: %s (판정 항목 %s)", self.cfg.module_id,
                         ", ".join(caps) or "없음 — 이벤트를 만들지 않습니다")
            self._registered = caps
            self.platform_seen = True
        return ok

    # ── 계약 2: 일감 ────────────────────────────────────────────────

    def reconcile(self, items: list[WorkItem]) -> None:
        """담당 카메라와 파이프라인을 맞춘다. 할당이 늘거나 줄면 여기서 따라간다."""
        wanted = {i.camera_id for i in items}
        now = time.monotonic()

        with self._lock:
            self.work = items
            for cam in list(self.pipelines):
                if cam in wanted:
                    continue
                pipe = self.pipelines.pop(cam)
                pipe.close(now)              # 켜져 있던 판정을 해제하고 보낸다
                log.info("카메라 %d 담당에서 빠졌습니다", cam)
            for cam in sorted(wanted):
                if cam in self.pipelines:
                    continue
                self.pipelines[cam] = CameraPipeline(
                    cam, self.cfg, self.publisher, self.settings.setup(cam),
                    on_judgement=self._note, snapshot=self._snapshot)
                log.info("카메라 %d 담당 시작 (보정 %s)", cam,
                         "있음" if self.settings.setup(cam).calibrated else "없음 — 판정 대기")
            cameras = sorted(self.pipelines)

        if cameras:
            self._subscribe(cameras)

    def _subscribe(self, cameras: list[int]) -> None:
        """담당 카메라의 `aivision/live/{camera_id}` 만 구독한다(계약 3장).

        담당이 없을 때 구독을 시작하지 않는 이유: SDK 는 카메라 목록이 비면 `+` 로
        전부 받는데, 카메라가 수십 대인 현장에서 쓰지도 않을 메시지를 초당 수백 건 푼다.
        """
        if not self._sub_started:
            self._sub_started = True
            self.subscriber.start(cameras=cameras,
                                  modules=self.cfg.src_modules or None)
            log.info("구독 시작: 카메라 %s · 발행자 %s",
                     ", ".join(str(c) for c in cameras),
                     ", ".join(self.cfg.src_modules) or "전부(자기 출력 제외)")
            return
        self.subscriber.retarget(cameras)

    # ── 계약 3: 입력 -> 판정 ────────────────────────────────────────

    def _on_frame(self, frame: Frame) -> None:
        """MQTT 스레드. 버퍼에 넣기만 한다(계약 3장).

        자기 출력을 먹지 않는다. `SRC_MODULES` 에 자기 id 가 잘못 들어가도, 라이브
        오버레이를 켜 두었어도 여기서 끊긴다 — 자기 박스를 다시 판정하면 트랙이 두 배가
        되고 되먹임이 생긴다.
        """
        if frame.module_id == self.cfg.module_id:
            return
        pipe = self.pipelines.get(frame.camera_id)
        if pipe is None:
            self.foreign += 1
            return
        pipe.offer(frame)

    def _judge_loop(self) -> None:
        interval = 1.0 / max(1.0, self.cfg.tick_hz)
        while not self.stop_event.wait(interval):
            now = time.monotonic()
            for cam, pipe in list(self.pipelines.items()):
                try:
                    pipe.tick(now)
                except Exception as exc:                        # noqa: BLE001
                    # 한 카메라의 사고가 다른 카메라를 멈추면 안 된다. 조용히 멈추는 것이
                    # 가장 찾기 어려운 고장이므로, 남기되 계속 돈다.
                    self.last_error = f"카메라 {cam}: {type(exc).__name__}: {exc}"[:200]
                    log.exception("카메라 %d 판정 중 예외", cam)

    # ── 계약 4: 생존 신고 ───────────────────────────────────────────

    def status(self) -> dict:
        with self._lock:
            pipes = list(self.pipelines.values())
        sub = self.subscriber.status()
        return {
            "cameras": sorted(p.camera_id for p in pipes),
            "calibrated": sorted(p.camera_id for p in pipes if p.calibrated),
            "items": self.cfg.capabilities,
            "labels": {"amr": self.cfg.amr_labels, "person": self.cfg.person_labels},
            "sources": self.cfg.src_modules or ["(전부)"],
            "received": sub.get("received", 0),
            "dropped": sub.get("dropped", 0),
            "foreign": self.foreign,
            "published": sum(p.published for p in pipes),
            "device": "rule",
            "errors": ({"judge": self.last_error} if self.last_error else {}),
            "per_camera": [p.status() for p in pipes],
        }

    # ── 설정 반영 ───────────────────────────────────────────────────

    def reload(self) -> None:
        """화면에서 설정을 고쳤다. 파일을 다시 읽어 그대로 반영한다.

        컨테이너를 다시 띄우게 만들면 현장에서 아무도 안 바꾼다. 트랙과 진행 중인
        판정은 버리지 않는다 — 임계값을 만지는 동안 알람이 끊기면 안 된다.
        """
        self.settings = self._load_settings()
        config_module.apply_settings(self.cfg, self.settings)
        with self._lock:
            for cam, pipe in self.pipelines.items():
                pipe.configure(self.cfg, self.settings.setup(cam))
        if self._registered != self.cfg.capabilities:
            self.register()

    # ── 증거 ────────────────────────────────────────────────────────

    def _snapshot(self, camera_id: int) -> str:
        """확정 순간의 스냅샷 한 장을 자기 볼륨에 남긴다(기획 7장).

        플랫폼의 `/api/stream/{id}/snapshot.jpg` 를 쓴다. 카메라에 직접 붙지 않는 이유:
        카메라 세션을 늘리지 않고, 계정도 알 필요가 없다.
        """
        root = self.cfg.evidence_dir
        url = f"{self.cfg.platform_url}/api/stream/{camera_id}/snapshot.jpg"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:      # noqa: S310
                data = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("증거 스냅샷을 받지 못했습니다 (%s): %s", url, exc)
            return ""
        if not data:
            return ""
        name = f"cam{camera_id}-{time.strftime('%Y%m%d-%H%M%S')}.jpg"
        try:
            root.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(data)
        except OSError as exc:
            log.warning("증거 스냅샷을 저장하지 못했습니다: %s", exc)
            return ""
        self._prune_evidence()
        log.info("증거 스냅샷: %s", name)
        return name

    def _prune_evidence(self) -> None:
        """오래된 것부터 지운다. 상한이 없으면 볼륨이 조용히 찬다."""
        root = self.cfg.evidence_dir
        try:
            files = sorted(root.glob("cam*.jpg"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        for path in files[:-self.cfg.evidence_keep] if self.cfg.evidence_keep else []:
            try:
                path.unlink()
            except OSError:
                pass

    def _note(self, j: Judgement) -> None:
        self.judgements.appendleft(j)

    # ── 기동 ────────────────────────────────────────────────────────

    def run(self) -> None:
        cfg = self.cfg
        self.publisher.start()

        self._judge_thread = threading.Thread(target=self._judge_loop, name="judge",
                                              daemon=True)
        self._judge_thread.start()

        while not self.stop_event.is_set():
            if self.register():
                break
            log.warning("플랫폼 등록 실패 — 5초 후 재시도 (%s)", cfg.platform_url)
            if self.stop_event.wait(5.0):
                return

        last_heartbeat = 0.0
        last_register = time.monotonic()
        while not self.stop_event.is_set():
            items = self.platform.work()
            if items is None:
                # 플랫폼이 안 보인다. 돌고 있는 파이프라인은 그대로 둔다 — 브로커는
                # 플랫폼 REST 와 무관하게 살아 있을 수 있고, 그동안에도 판정은 해야 한다.
                self.platform_seen = False
                log.debug("일감을 받지 못했습니다 — 현재 담당을 유지합니다")
            else:
                if not self.platform_seen:
                    # 플랫폼이 재시작했을 수 있다. 등록은 갱신이라 다시 불러도 안전하다.
                    self.register()
                    last_register = time.monotonic()
                self.platform_seen = True
                if not items:
                    log.info("할당된 카메라가 없습니다. 플랫폼에서 이 모듈에 카메라를 "
                             "할당하세요.")
                self.reconcile(items)

            now = time.monotonic()
            if now - last_heartbeat >= cfg.heartbeat_sec:
                last_heartbeat = now
                self.platform.heartbeat(self.status())
            if now - last_register >= 300.0:
                # 5분마다 한 번 더 등록한다. 플랫폼이 죽지 않은 채 DB 만 갈린 경우
                # (재설치·복구) 위의 '안 보이다가 보이면' 조건에 걸리지 않아, 사람이
                # 손대기 전까지 모듈 목록에서 사라진 상태로 남는다.
                last_register = now
                self.register()

            self.stop_event.wait(cfg.work_poll_sec)

        self.shutdown()

    def shutdown(self) -> None:
        log.info("종료 중…")
        now = time.monotonic()
        with self._lock:
            for pipe in self.pipelines.values():
                try:
                    pipe.close(now)
                except Exception:                               # noqa: BLE001
                    log.debug("파이프라인 정리 중 오류", exc_info=True)
            self.pipelines.clear()
        if self._sub_started:
            self.subscriber.stop()
        self.publisher.stop()
        log.info("종료 완료")

    # ── 화면이 쓰는 것 ──────────────────────────────────────────────

    def platform_json(self, path: str) -> dict | list | None:
        """플랫폼 REST 를 대신 불러 준다.

        화면이 직접 부르지 않는 이유: 모듈 화면은 iframe 안에서 **모듈 주소**로 떠 있어
        플랫폼은 다른 오리진이다. 브라우저가 막는다. 여기서 중계하면 화면은 자기 주소만
        알면 된다. 플랫폼이 안 보이면 None — 화면은 빈 목록으로 내려앉는다(계약 4장).
        """
        url = f"{self.cfg.platform_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:      # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:                                      # noqa: BLE001
            log.warning("플랫폼 조회 실패 (%s): %s", url, exc)
            return None

    def overlay(self, camera_id: int) -> dict | None:
        """카메라 한 대의 그림 한 벌. 화면이 초당 몇 번 부른다.

        판정 스레드가 굳혀 둔 것을 그대로 돌려준다 — 여기서 계산하지 않는다.
        """
        pipe = self.pipelines.get(int(camera_id))
        return None if pipe is None else pipe.overlay()

    def platform_stream(self, path: str):
        """플랫폼 MJPEG 를 그대로 흘려보낸다(중계).

        화면이 플랫폼에 직접 붙지 못하기 때문이다(오리진이 다르다). 여기서 중계하면
        모듈 화면은 자기 주소만 알면 되고, 그 위에 구역을 겹쳐 그릴 수 있다.

        제너레이터를 돌려준다 — 통째로 읽어 두면 스트림이 스트림이 아니게 된다.
        """
        url = f"{self.cfg.platform_url}{path}"
        try:
            resp = urllib.request.urlopen(url, timeout=10)             # noqa: S310
        except Exception as exc:                                       # noqa: BLE001
            log.warning("플랫폼 스트림을 열지 못했습니다 (%s): %s", url, exc)
            return None

        content_type = resp.headers.get("Content-Type", "multipart/x-mixed-replace")

        def chunks():
            try:
                while True:
                    data = resp.read(16384)
                    if not data:
                        break
                    yield data
            except (OSError, ValueError):
                # 브라우저가 창을 닫으면 여기서 끊긴다. 정상이다.
                log.debug("스트림 중계 종료", exc_info=True)
            finally:
                resp.close()

        return (chunks(), content_type)

    def platform_bytes(self, path: str) -> tuple[bytes, str] | None:
        """스냅샷처럼 JSON 이 아닌 것. 보정 화면이 쓴다."""
        url = f"{self.cfg.platform_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:      # noqa: S310
                return (resp.read(), resp.headers.get("Content-Type", "image/jpeg"))
        except Exception as exc:                                      # noqa: BLE001
            log.warning("플랫폼 이미지 조회 실패 (%s): %s", url, exc)
            return None

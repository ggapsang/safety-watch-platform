"""카메라 ONVIF 메타데이터 -> 라이브 박스.

첫 번째 실사용 모듈이다. 우리가 추론하지 않는다 — 카메라 안에서 이미 돌고 있는 분석
(WiseAI 등)이 만든 박스를 RTSP 메타데이터 트랙에서 읽어 화면에 그리기만 한다.

**이 모듈은 이벤트를 만들지 않는다.** `aivision/live/{camera_id}` 만 발행한다.
사람이 서 있는 동안 초당 여러 번 나오는 판독을 이벤트로 쌓으면 DB 가 무너지고, 그것은
'라이브 박스는 이벤트가 아니다'라는 설계 원칙을 정면으로 어긴다. 무엇을 이벤트로 볼지는
나중에 따로 정한다(예: 지정 구역에 일정 시간 이상 머무름) — 그때는 그 판단을 하는 모듈이
`aivision/detect/...` 로 발행하면 되고, 이 모듈은 그대로 둔다.

영상은 카메라가 아니라 **미디어 서버**에서 가져온다. 일감(`/work`)이 준 주소를 그대로
쓰므로 카메라 계정을 알 필요가 없고, 카메라 세션도 늘지 않는다. 미디어 서버가 데이터
트랙을 그대로 중계해 주는 것을 확인하고 이 구조를 택했다.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from typing import Iterator

sys.path.insert(0, "/app")                     # _sdk 가 옆에 놓인다

import config as config_module                 # noqa: E402
import metadata                                # noqa: E402
from _sdk import Runner, Source, WorkItem, configure_logging, main_loop  # noqa: E402

log = logging.getLogger("onvif-meta")

READ_CHUNK = 8192


class MetadataSource(Source):
    """RTSP 데이터 트랙 하나를 ffmpeg 로 열어 XML 을 읽는다.

    ffmpeg 를 쓰는 이유: RTSP/RTP 재조립·인증·TCP 인터리브를 직접 짜는 것은 우리가 만들려는
    가치가 아니다. 여기서는 표준 출력으로 나오는 XML 조각을 이어 붙이기만 한다.
    """

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self._buffer = b""
        self._logged_topics: set[str] = set()

    @property
    def device(self) -> str:
        return "camera-metadata"

    def open(self, item: WorkItem) -> None:
        if not item.rtsp:
            raise RuntimeError("일감에 스트림 주소가 없습니다")
        cmd = [
            "ffmpeg", "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", item.rtsp,
            # 데이터 트랙만 가져온다. 영상은 디코딩조차 하지 않는다.
            "-map", "0:d:0", "-c", "copy", "-f", "data", "-",
        ]
        log.info("카메라 %d 메타데이터 트랙 열기: %s", item.camera_id, item.rtsp)
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._buffer = b""

    def close(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def boxes(self, item: WorkItem, stop: threading.Event) -> Iterator[list[dict]]:
        proc = self.proc
        if proc is None or proc.stdout is None:
            raise RuntimeError("메타데이터 트랙이 열리지 않았습니다")

        while not stop.is_set():
            chunk = proc.stdout.read(READ_CHUNK)
            if not chunk:
                err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace")
                raise RuntimeError(f"메타데이터 트랙이 끊겼습니다: {err.strip()[:200]}")

            self._buffer += chunk
            docs, self._buffer = metadata.split_documents(self._buffer)
            for doc in docs:
                boxes = metadata.parse_boxes(doc, self.cfg.class_map,
                                             keep_unmapped=self.cfg.keep_unmapped,
                                             skip=self.cfg.skip_classes)
                if boxes is None:
                    self._note_event(doc)
                    continue          # 박스 이야기가 아닌 문서 — 화면을 건드리지 않는다
                yield boxes

    def _note_event(self, doc: bytes) -> None:
        """섞여 오는 ONVIF 이벤트 알림은 처음 보는 토픽만 한 번 기록한다.

        이 모듈은 이벤트를 만들지 않지만, 카메라가 무엇을 알려 오는지는 알아 두면
        나중에 바인딩을 만들 때 쓸모가 있다.
        """
        topic = metadata.event_topic(doc)
        if topic and topic not in self._logged_topics:
            self._logged_topics.add(topic)
            log.info("카메라 이벤트 토픽 관찰: %s", topic)


def main(argv: list[str]) -> int:
    configure_logging()
    cfg = config_module.load()
    runner = Runner(
        cfg,
        make_source=lambda item: MetadataSource(cfg),
        # on_boxes 를 주지 않는다 = 라이브 박스만 나간다. 이벤트는 만들지 않는다.
        description="카메라 ONVIF 메타데이터를 읽어 박스만 그린다 (이벤트 없음)",
    )
    return main_loop(runner)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

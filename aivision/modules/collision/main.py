"""충돌 위험 모듈 — AMR 과 사람의 접근·충돌을 본다.

**이 모듈은 영상을 열지 않는다.** `aivision/live/{camera_id}` 에 이미 나와 있는 박스를
받아 판정만 한다(계약 3장). 같은 프레임을 두 번 추론할 이유가 없고, 사람과 AMR 은
다른 모듈이 이미 잘 잡고 있다. 그래서 이 이미지에는 모델도 opencv 도 ffmpeg 도 없다.

받은 박스에는 `track_id` 가 없으므로(계약 2장) **트래킹은 이 모듈의 몫**이다. 그것이
이 모듈의 절반이다 — 나머지 절반은 바닥 평면으로 옮겨 미터로 재는 일이다.

한 프로세스에 둘을 담는다.
  · 판정·배관   Service. 배경 스레드에서 돈다. 결과는 MQTT 로 발행한다.
  · API·화면    FastAPI/uvicorn. 메인 스레드에서 돈다.

실행
  python main.py              판정 + 화면
  python main.py --no-serve   화면 없이 판정만 (다른 곳에서 보정 파일을 넣어 줄 때)
"""

from __future__ import annotations

import logging
import sys
import threading

sys.path.insert(0, "/app")                     # _sdk 가 옆에 놓인다

import config as config_module                 # noqa: E402
from _sdk import configure_logging, main_loop  # noqa: E402
from service import Service                    # noqa: E402

log = logging.getLogger("collision")


def main(argv: list[str]) -> int:
    configure_logging()
    cfg = config_module.load()
    serve = "--no-serve" not in argv

    service = Service(cfg)
    log.info("모듈 %s 기동 — 발행자 %s · AMR %s · 사람 %s", cfg.module_id,
             ", ".join(cfg.src_modules) or "전부(자기 출력 제외)",
             ", ".join(cfg.amr_labels) or "(미지정)",
             ", ".join(cfg.person_labels) or "(미지정)")

    if not serve:
        # SDK 의 main_loop 는 stop_event·run·shutdown 만 본다. Service 가 그 모양이라
        # 그대로 쓴다 — SIGTERM 처리를 두 벌 만들 이유가 없다.
        return main_loop(service)

    import uvicorn

    import api

    # 순서가 중요하다 — 배관이 먼저 떠야 화면이 첫 폴링에서 상태를 볼 수 있다.
    worker = threading.Thread(target=service.run, name="service", daemon=True)
    worker.start()

    app = api.create_app(cfg, service)
    log.info("모듈 화면: %s (컨테이너 안에서는 :%d)", cfg.public_url, cfg.serve_port)

    try:
        uvicorn.run(app, host="0.0.0.0", port=cfg.serve_port, log_level="warning")
    finally:
        service.stop_event.set()
        worker.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

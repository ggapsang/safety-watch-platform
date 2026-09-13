"""모듈 SDK — 플랫폼과 말을 맞추는 배관.

두 번째 모듈(onvif_meta)이 생겨서 yolo 사이드카의 배관을 여기로 뽑았다. 하나뿐일 때
미리 만들면 그 하나에 맞춘 추상화가 된다는 이유로 미뤄 뒀던 것이다.

여기에는 **무엇을 탐지하는지에 대한 지식이 없다.** 모듈이 하는 일은 `Source` 하나를
구현하는 것뿐이고, 등록·일감 폴링·워커 수명주기·발행·heartbeat 는 전부 SDK 가 진다.

  모듈 계약 (플랫폼 쪽 api/modules.py 와 짝)
    1. 등록      POST /api/modules
    2. 일감      GET  /api/modules/{id}/work        카메라별 RTSP 주소 + 옵션
    3. 발행      MQTT aivision/detect/{cam}/{module}   이벤트 (전이 순간만)
                 MQTT aivision/live/{cam}              라이브 박스 (적재 안 됨)
    4. 생존      POST /api/modules/{id}/heartbeat

판정 결과를 REST 로 돌려주지 않는다. 카메라 엣지든 우리 모듈이든 같은 인바운드 바인딩
문을 지난다(매니페스토 4번). 이 SDK 를 쓴다고 특권 통로가 생기지 않는다.

**입력이 영상뿐인 것은 아니다.** `aivision/live/{cam}` 은 공개 토픽이라 다른 플러그인이
낸 박스를 받아 쓸 수 있다(`LiveSubscriber`). 규칙을 따지는 플러그인은 영상을 다시 열지
않고 남의 판독을 먹는 편이 낫다 — 같은 프레임을 두 번 추론할 이유가 없고, 사람처럼
카메라가 이미 잘 잡는 것을 다시 학습할 이유도 없다. 자세한 것은
`docs/플러그인-만들기.md` 를 보라.
"""

from .config import BaseConfig, class_map_from_env, env, flag, num
from .debounce import Debouncer, Transition
from .platform import Platform, WorkItem
from .publisher import Publisher
from .runner import (Runner, Source, SourceWorker, configure_logging,
                     main_loop)
from .subscriber import Frame, LiveSubscriber, parse_frame

__all__ = [
    "BaseConfig", "class_map_from_env", "env", "flag", "num",
    "Debouncer", "Transition",
    "Platform", "WorkItem",
    "Publisher",
    "Frame", "LiveSubscriber", "parse_frame",
    "Runner", "Source", "SourceWorker", "configure_logging", "main_loop",
]

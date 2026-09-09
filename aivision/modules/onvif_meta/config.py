"""이 모듈의 설정.

공통 항목(플랫폼 주소·브로커·발행 주기)은 SDK 가 읽는다. 여기서는 이 모듈만의 것을 더한다.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

sys.path.insert(0, "/app")

from _sdk import BaseConfig, class_map_from_env, env, flag  # noqa: E402


@dataclass
class Config(BaseConfig):
    module_id: str = "camera-meta"
    module_name: str = "카메라 메타데이터"
    # 카메라가 붙인 클래스 이름 -> 화면에 쓸 이름. 비우면 원래 이름을 그대로 쓴다.
    # 예: {"Human": "사람", "Face": "얼굴"}
    class_map: dict[str, str] = field(default_factory=dict)
    # 표에 없는 클래스도 그릴지. 이 모듈은 이벤트를 만들지 않으므로 기본은 다 그린다 —
    # 무엇이 잡히는지 눈으로 보는 것이 이 모듈의 용도다.
    keep_unmapped: bool = True
    # 그리지 않을 카메라 클래스(소문자로 보관). 사람 하나에 Human·Head·Face 가 겹쳐
    # 나오므로 기본적으로 안쪽 둘을 빼는 편이 화면이 깔끔하다.
    skip_classes: set[str] = field(default_factory=set)

    @property
    def capabilities(self) -> list[str]:
        """이 모듈은 판정 항목을 갖지 않는다.

        SDK 기본 구현은 class_map 의 값을 탐지 항목 코드로 알리는데, 여기 class_map 은
        '화면에 뭐라고 쓸까'일 뿐 이벤트 항목이 아니다. 그것을 판정 항목으로 등록하면
        화면에 '이 모듈이 사람을 판정한다'고 잘못 뜬다. 박스만 그리는 모듈이다.
        """
        return []


def load() -> Config:
    cfg = Config()
    cfg.load_base()
    cfg.class_map = class_map_from_env("CLASS_MAP")
    cfg.keep_unmapped = flag("KEEP_UNMAPPED", True)
    cfg.skip_classes = {c.strip().lower() for c in env("SKIP_CLASSES").split(",") if c.strip()}
    return cfg

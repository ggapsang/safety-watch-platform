"""모듈 설정 — 화면에서 고치고 파일에 남긴다.

**왜 파일인가.** 이 모듈에는 DB 가 없다. 그렇다고 플랫폼에 넣으면 코어가 YOLO 를 알게
된다(매니페스토 2번) — 모델 파일이니 클래스니 하는 것은 이 모듈만의 사정이다. 그래서
모델이 놓이는 볼륨(`/models`)에 JSON 하나로 둔다. 모델과 그 모델의 설정이 같은 곳에
있으면 볼륨째 옮겨도 따라간다.

**env 와의 관계.** env 는 '처음 값' 이고 파일은 '사람이 고친 값' 이다. 파일에 있는 값이
이긴다. 파일이 없으면 env 그대로 돈다 — 화면에 한 번도 안 들어간 현장도 그대로 동작해야
한다.

**클래스 표가 왜 모델별인가.** 모델을 바꾸면 클래스가 통째로 달라진다. 한 벌만 두면
모델을 갈아 끼울 때마다 이름과 항목 매핑을 다시 적어야 하고, 되돌릴 때도 마찬가지다.
모델 이름을 열쇠로 따로 담아 두면 왔다 갔다 해도 각자의 표가 남는다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FILENAME = "module.json"
VERSION = 1

# 화면에서 고칠 수 있는 값과 그 범위. 범위를 여기 한 곳에 둔다 —
# 화면과 API 가 따로 검사하면 언젠가 서로 다른 값을 통과시킨다.
TUNING: dict[str, tuple[float, float]] = {
    "conf_thres": (0.01, 0.99),      # 이 점수 미만 후보는 추론 단계에서 버린다
    "iou_thres": (0.01, 0.99),       # NMS 겹침 기준
    "min_conf": (0.0, 0.99),         # 이벤트로 올릴 최소 점수(판정 단계)
    "min_box_px": (0.0, 2000.0),     # sqrt(w*h) 픽셀. 작은 오탐을 거른다
    "sample_fps": (0.2, 30.0),       # 초당 몇 장이나 보나
}


@dataclass
class ClassRow:
    """모델 클래스 한 줄.

    `key`  모델이 들고 온 이름. 이름이 없는 모델이면 인덱스 문자열("0","1",…)이다.
    `alias` 사람이 붙인 이름. 화면(라이브 박스·이벤트 상세)에 이것이 그려진다.
            이름을 못 읽은 모델에서는 '메모' 노릇을 하고, 읽은 모델에서는 '별칭' 이 된다.
            비우면 key 를 그대로 쓴다.
    `item`  플랫폼 탐지 항목 코드. 비우면 **이벤트를 만들지 않는다** — 박스만 그린다.
            보기만 하고 적재는 않는 클래스가 실제로 있다(사람·차량 같은 배경 객체).
    """

    key: str
    alias: str = ""
    item: str = ""

    @property
    def label(self) -> str:
        return self.alias or self.key

    def to_dict(self) -> dict:
        return {"key": self.key, "alias": self.alias, "item": self.item}


@dataclass
class Settings:
    active_model: str = ""
    # 사람이 화면에서 추론을 꺼 둔 상태.
    #
    # active_model 을 비우는 것으로 대신하지 않는다. 빈 값은 '아직 고르지 않았다'(그래서
    # env 를 따른다)는 뜻이라 '일부러 껐다'와 구별되지 않는다. 둘을 합치면 껐는데 재시작할
    # 때 env 의 모델로 되살아난다.
    #
    # 끈 상태는 dry-run 과도 다르다. dry-run 은 합성 박스를 **발행한다** — 배관을 보려고
    # 만든 모드다. 끈 것은 아무것도 내보내지 않아야 한다.
    stopped: bool = False
    tuning: dict[str, float] = field(default_factory=dict)
    # 모델 파일 이름 -> 클래스 표. 순서가 곧 클래스 인덱스라 리스트로 둔다.
    models: dict[str, list[ClassRow]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    # ── 조회 ────────────────────────────────────────────────────────

    def rows(self, model: str) -> list[ClassRow]:
        return self.models.get(model, [])

    def alias_map(self, model: str) -> dict[str, str]:
        """key -> 화면에 쓸 이름. 라이브 박스와 이벤트 박스가 함께 쓴다."""
        return {r.key: r.label for r in self.rows(model) if r.alias}

    def class_map(self, model: str) -> dict[str, str]:
        """key -> 플랫폼 항목 코드. 예전 CLASS_MAP env 와 같은 모양이다."""
        return {r.key: r.item for r in self.rows(model) if r.item}

    # ── 갱신 ────────────────────────────────────────────────────────

    def ensure_rows(self, model: str, keys: list[str]) -> bool:
        """모델이 실제로 들고 있는 클래스에 표를 맞춘다. 바뀌었으면 True.

        모델을 처음 올렸을 때 표를 만들고, 같은 이름으로 다른 모델을 덮어썼을 때
        없어진 클래스를 지운다. 사람이 적어 둔 이름과 매핑은 key 가 같으면 살린다 —
        모델을 다시 내보냈다는 이유로 적어 둔 것을 날리면 안 된다.
        """
        before = [r.to_dict() for r in self.rows(model)]
        keep = {r.key: r for r in self.rows(model)}
        self.models[model] = [keep.get(k, ClassRow(key=k)) for k in keys]
        return [r.to_dict() for r in self.models[model]] != before

    def set_tuning(self, values: dict[str, Any]) -> dict[str, float]:
        """범위 안으로 자른 뒤 저장한다. 범위 밖 값으로 거절하지 않는다 —
        슬라이더를 끝까지 민 것을 오류로 돌려주면 화면이 멈춘 것처럼 보인다."""
        for name, (lo, hi) in TUNING.items():
            if name not in values or values[name] is None:
                continue
            try:
                self.tuning[name] = min(hi, max(lo, float(values[name])))
            except (TypeError, ValueError):
                log.warning("조정값 %s 를 숫자로 읽지 못했습니다: %r", name, values[name])
        return dict(self.tuning)

    # ── 직렬화 ──────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "version": VERSION,
            "active_model": self.active_model,
            "stopped": self.stopped,
            "tuning": self.tuning,
            "notes": self.notes,
            "models": {m: [r.to_dict() for r in rows] for m, rows in self.models.items()},
        }


def _parse(data: dict) -> Settings:
    s = Settings()
    s.active_model = str(data.get("active_model") or "")
    s.stopped = bool(data.get("stopped"))
    raw_tuning = data.get("tuning") or {}
    for name, (lo, hi) in TUNING.items():
        if name in raw_tuning:
            try:
                s.tuning[name] = min(hi, max(lo, float(raw_tuning[name])))
            except (TypeError, ValueError):
                pass
    s.notes = {str(k): str(v) for k, v in (data.get("notes") or {}).items()}
    for model, rows in (data.get("models") or {}).items():
        if not isinstance(rows, list):
            continue
        s.models[str(model)] = [
            ClassRow(key=str(r.get("key", "")), alias=str(r.get("alias", "") or ""),
                     item=str(r.get("item", "") or ""))
            for r in rows if isinstance(r, dict) and r.get("key") is not None
        ]
    return s


def load(models_dir: Path) -> Settings:
    """설정을 읽는다. 없거나 깨졌으면 빈 설정 — 기동을 막지 않는다.

    설정 파일 하나 때문에 추론이 안 뜨면 현장에서 손쓸 방법이 없다. env 값만으로도
    모듈은 제 할 일을 한다.
    """
    path = models_dir / FILENAME
    if not path.is_file():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("설정 파일을 읽지 못했습니다 (%s): %s — env 값으로 진행합니다", path, exc)
        return Settings()
    if not isinstance(data, dict):
        log.error("설정 파일의 최상위가 객체가 아닙니다 (%s) — 무시합니다", path)
        return Settings()
    return _parse(data)


def save(models_dir: Path, s: Settings) -> None:
    """원자적으로 쓴다. 쓰다 죽어도 반쯤 쓰인 파일이 남지 않는다."""
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / FILENAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s.to_dict(), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    tmp.replace(path)

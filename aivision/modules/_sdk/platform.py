"""플랫폼 REST 호출.

실패를 예외로 올리지 않고 None 을 돌려준다 — 플랫폼이 재시작 중이어도 모듈은 계속 살아
있어야 한다. 영상과 브로커는 플랫폼 REST 와 무관하게 흐를 수 있다.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class WorkItem:
    """플랫폼이 준 일감 하나 — '이 카메라를 이 주소에서 이 옵션으로 보라'."""

    camera_id: int
    camera_name: str
    rtsp: str
    options: dict = field(default_factory=dict)


class Platform:
    def __init__(self, base_url: str, module_id: str, timeout: float = 8.0) -> None:
        self.base = base_url.rstrip("/")
        self.module_id = module_id
        self.timeout = timeout

    def _call(self, method: str, path: str, body: dict | None = None) -> dict | None:
        url = f"{self.base}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            log.warning("%s %s -> HTTP %d %s", method, path, exc.code, detail)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("%s %s -> 연결 실패: %s", method, path, exc)
        except json.JSONDecodeError:
            log.warning("%s %s -> 응답이 JSON 이 아닙니다", method, path)
        return None

    def register(self, name: str, capabilities: list[str], kind: str = "sidecar",
                 description: str = "") -> bool:
        """등록. 같은 id 로 다시 불러도 갱신이라 재시작마다 실패하지 않는다."""
        return self._call("POST", "/api/modules", {
            "id": self.module_id, "name": name, "kind": kind,
            "description": description, "capabilities": capabilities}) is not None

    def work(self) -> list[WorkItem] | None:
        """무엇을 볼지 물어본다. None 은 '플랫폼이 안 보인다'이지 '일감 없음'이 아니다."""
        data = self._call("GET", f"/api/modules/{self.module_id}/work")
        if data is None:
            return None
        return [WorkItem(camera_id=int(row["camera_id"]),
                         camera_name=row.get("camera_name") or "",
                         rtsp=row.get("rtsp") or "",
                         options=row.get("options") or {})
                for row in data.get("items", [])]

    def heartbeat(self, status: dict) -> None:
        self._call("POST", f"/api/modules/{self.module_id}/heartbeat", status)

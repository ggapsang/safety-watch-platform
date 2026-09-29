"""감시 기록(metrics/*.jsonl)에서 추이를 뽑아 본다.

    docker compose exec ops-watchdog python3 trend.py              오늘
    docker compose exec ops-watchdog python3 trend.py 2026-09-29   날짜 지정
"""
import json
import sys
from datetime import datetime
from pathlib import Path

day = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
rows = [json.loads(line) for line in Path(f"/ops/metrics/{day}.jsonl").read_text(
    encoding="utf-8").splitlines() if line.strip()]
rows = [r for r in rows if (r.get("app") or {}).get("memory")]
if not rows:
    sys.exit("기록 없음")

step = max(1, len(rows) // 12)
print(f"{day}  표본 {len(rows)}개  {rows[0]['ts'][11:]} ~ {rows[-1]['ts'][11:]}")
print(f"{'시각':<9}{'base-app':>10}{'스레드':>6}{'mod-yolo':>10}{'스레드':>6}"
      f"{'yolo재연결':>9}{'카메라':>6}{'VM여유':>9}")
for r in rows[::step] + ([rows[-1]] if (len(rows) - 1) % step else []):
    c, a, y = r["containers"], r["app"], r.get("yolo") or {}
    print(f"{r['ts'][11:19]:<9}"
          f"{c['base-app']['mem_mb']:>8.0f}MB{c['base-app']['pids']:>6}"
          f"{c['mod-yolo']['mem_mb']:>8.0f}MB{c['mod-yolo']['pids']:>6}"
          f"{y.get('reconnects', 0):>9}"
          f"{a['streams']['connected']:>4}/{a['streams']['total']}"
          f"{r['vm']['MemAvailable']:>7}MB")

def since_start(svc: str) -> list[dict]:
    """그 컨테이너가 마지막으로 뜬 뒤의 표본만. 재기동을 사이에 두고 재면 기울기가 거짓이 된다.

    뜬 직후 2분은 뺀다 — 카메라가 붙고 모델이 올라가는 동안의 상승은 누수가 아니다.
    """
    cut = 0
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1]["containers"].get(svc, {}), rows[i]["containers"].get(svc, {})
        # 재기동 신호: 재시작 횟수가 늘었거나, 메모리가 절반 아래로 떨어졌다
        if (cur.get("restarts", 0) > prev.get("restarts", 0)
                or cur.get("mem_mb", 0) < prev.get("mem_mb", 0) * 0.5):
            cut = i
    return rows[cut + 4:] if len(rows) - cut > 6 else []


for svc in ("base-app", "mod-yolo"):
    part = since_start(svc)
    if len(part) < 2:
        print(f"{svc:<9} 뜬 지 얼마 안 돼 기울기를 잴 수 없습니다")
        continue
    a, b = part[0], part[-1]
    hours = (datetime.fromisoformat(b["ts"]) - datetime.fromisoformat(a["ts"])).total_seconds() / 3600
    d = b["containers"][svc]["mem_mb"] - a["containers"][svc]["mem_mb"]
    line = f"{svc:<9} {a['ts'][11:16]}~{b['ts'][11:16]}  메모리 {d:+.0f}MB → 시간당 {d / hours:+.0f}MB"
    if svc == "mod-yolo":
        yr = (b.get("yolo") or {}).get("reconnects", 0) - (a.get("yolo") or {}).get("reconnects", 0)
        line += f"  · 재연결 {yr}회 → 시간당 {yr / hours:.0f}회"
    print(line)

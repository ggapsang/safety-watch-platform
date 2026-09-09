"""py3.13 / torch2.6 / numpy2.x 환경에서 2022년 YOLOv7 레포를 돌리기 위한 호환성 패치."""
import builtins
import numpy as np
import torch

# (격리 env(py3.10/torch1.13/numpy1.23)에서는 불필요 — no-op)

# numpy 2.x 에서 제거된 별칭 복원
for _n, _t in (("int", int), ("float", float), ("bool", bool), ("object", object),
               ("str", str), ("long", int)):
    if not hasattr(np, _n):
        setattr(np, _n, _t)

# numpy 2.0: np.trapz 제거 → np.trapezoid 로 대체(동일 시그니처).
# yolov7 utils/metrics.py 의 mAP 적분(compute_ap)에서 사용 → 검증 단계 크래시 방지.
if not hasattr(np, "trapz") and hasattr(np, "trapezoid"):
    np.trapz = np.trapezoid

# torch 2.6: torch.load 기본 weights_only=True → 레거시 체크포인트/캐시 로드 실패 방지
_orig_load = torch.load
def _load(*a, **k):
    k.setdefault("weights_only", False)
    return _orig_load(*a, **k)
torch.load = _load

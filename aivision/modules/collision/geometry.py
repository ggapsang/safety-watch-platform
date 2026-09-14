"""좌표 변환과 기하 판정 — 정규화 이미지 좌표를 바닥 월드 좌표로.

**왜 픽셀이 아니라 정규화 좌표에서 캘리브레이션하는가.** 계약이 주는 박스는 0~1 이고
프레임 크기는 받는 쪽이 모른다(계약 2장). 픽셀로 환산하려면 발행자의 스트림 프로파일을
알아야 하는데, 그것은 언제든 바뀐다. 그래서 호모그래피 H 를 **정규화 이미지 좌표계에서**
잡는다 — 프로파일이 1080p 에서 4K 로 바뀌어도 같은 H 가 그대로 쓸모 있다.

여기 있는 것은 전부 순수 함수다. 상태도 없고 스레드도 모른다 — 그래야 tests.py 에서
브로커도 카메라도 없이 검증할 수 있다.
"""

from __future__ import annotations

import logging
import math

import numpy as np

log = logging.getLogger(__name__)

# 호모그래피를 쓸 수 없다고 판단하는 선. 4점이 거의 한 줄에 있거나 같은 점을 두 번
# 찍으면 행렬이 특이해지고, 그때 나오는 월드 좌표는 숫자이긴 하지만 뜻이 없다.
MIN_DET = 1e-9


def foot_point(box: dict) -> tuple[float, float]:
    """발 접지점 — 박스 하단 중앙.

    바닥 평면으로 옮길 수 있는 유일한 점이다. 박스 중심을 쓰면 사람 키만큼 앞으로
    밀려 나오고, 그 오차가 거리 임계값(수십 cm)보다 크다.
    """
    return ((float(box["x1"]) + float(box["x2"])) / 2.0, float(box["y2"]))


def _normalize(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Hartley 정규화 — 중심을 옮기고 평균 거리를 sqrt(2) 로 맞춘다.

    이미지 좌표는 0~1 이고 월드 좌표는 미터(수~수십)라 두 축의 크기가 열 배 넘게 다르다.
    그대로 SVD 에 넣으면 조건수가 나빠져 4점이 조금만 흔들려도 H 가 크게 튄다.
    """
    center = pts.mean(axis=0)
    shifted = pts - center
    mean_dist = float(np.mean(np.linalg.norm(shifted, axis=1)))
    scale = math.sqrt(2.0) / mean_dist if mean_dist > 1e-12 else 1.0
    T = np.array([[scale, 0.0, -scale * center[0]],
                  [0.0, scale, -scale * center[1]],
                  [0.0, 0.0, 1.0]])
    return shifted * scale, T


def homography_from_points(pairs: list[tuple[tuple[float, float],
                                             tuple[float, float]]]) -> np.ndarray | None:
    """((u,v) 정규화 이미지, (X,Y) 월드 미터) 대응 4쌍 이상 -> H (3x3).

    풀 수 없으면 None 이다. 예외를 올리지 않는 이유: 사람이 화면에서 점을 찍다가 만
    상태가 정상적인 중간 상태이고, 그때마다 모듈이 죽으면 안 된다.
    """
    if len(pairs) < 4:
        return None
    img = np.array([p[0] for p in pairs], dtype=float)
    world = np.array([p[1] for p in pairs], dtype=float)
    if not (np.isfinite(img).all() and np.isfinite(world).all()):
        return None

    img_n, T_img = _normalize(img)
    world_n, T_world = _normalize(world)

    rows = []
    for (u, v), (X, Y) in zip(img_n, world_n):
        rows.append([-u, -v, -1.0, 0.0, 0.0, 0.0, X * u, X * v, X])
        rows.append([0.0, 0.0, 0.0, -u, -v, -1.0, Y * u, Y * v, Y])
    A = np.array(rows, dtype=float)

    try:
        _, _, vt = np.linalg.svd(A)
    except np.linalg.LinAlgError:
        return None
    H_n = vt[-1].reshape(3, 3)

    try:
        H = np.linalg.inv(T_world) @ H_n @ T_img
    except np.linalg.LinAlgError:
        return None
    if abs(H[2, 2]) < 1e-12 or abs(float(np.linalg.det(H))) < MIN_DET:
        return None
    return H / H[2, 2]


def project(H: np.ndarray, u: float, v: float) -> tuple[float, float] | None:
    """정규화 이미지 좌표 -> 바닥 월드 좌표(미터).

    분모가 0 에 가까우면 None 이다. 그 점은 카메라의 수평선 근처라 바닥 평면 위의
    어느 점으로도 대응되지 않는다 — 큰 수를 내놓느니 '모른다' 가 낫다.
    """
    denom = H[2, 0] * u + H[2, 1] * v + H[2, 2]
    if abs(denom) < 1e-9:
        return None
    x = (H[0, 0] * u + H[0, 1] * v + H[0, 2]) / denom
    y = (H[1, 0] * u + H[1, 1] * v + H[1, 2]) / denom
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return (x, y)


def reprojection_error(H: np.ndarray,
                       pairs: list[tuple[tuple[float, float],
                                         tuple[float, float]]]) -> float:
    """보정 점들이 얼마나 잘 맞는지(미터, 평균).

    화면에 이 값을 보여 준다. 사람이 점을 대충 찍었는지 제대로 찍었는지 알 수 있는
    유일한 단서다 — 숫자 없이 '저장됨' 만 보여 주면 30cm 틀린 보정으로 몇 달을 돈다.
    """
    if not pairs:
        return 0.0
    total = 0.0
    for (u, v), (X, Y) in pairs:
        got = project(H, u, v)
        if got is None:
            return float("inf")
        total += math.hypot(got[0] - X, got[1] - Y)
    return total / len(pairs)


# ────────────────────────────────────────────────────────── 위험 기하

def corridor_hit(p_amr: tuple[float, float], heading: tuple[float, float],
                 p_h: tuple[float, float], half_w: float, length: float,
                 back: float = 0.0) -> tuple[bool, float, float]:
    """사람이 AMR 진행 통로(corridor) 안에 있는가. -> (안에 있나, 전방거리, 측면거리)

    통로는 AMR 진행 방향으로 뻗은 직사각형이다. 원형 거리만 보면 AMR **뒤쪽** 1m 에
    선 사람과 **앞쪽** 1m 에 선 사람이 같은 위험이 되는데, 현장에서는 전혀 다르다.
    """
    dx, dy = p_h[0] - p_amr[0], p_h[1] - p_amr[1]
    hx, hy = heading
    norm = math.hypot(hx, hy)
    if norm < 1e-9:
        return (False, 0.0, math.hypot(dx, dy))
    hx, hy = hx / norm, hy / norm
    ahead = dx * hx + dy * hy                 # 진행 방향 성분
    lateral = abs(-dx * hy + dy * hx)         # 통로 중심선에서 옆으로 벗어난 거리
    inside = (-back <= ahead <= length) and lateral <= half_w
    return (inside, ahead, lateral)


def sweep_tau(p_amr: tuple[float, float], v_amr: tuple[float, float],
              p_h: tuple[float, float], radius: float, growth: float,
              horizon: float) -> float | None:
    """AMR 전방 스윕이 사람의 최악 도달 영역에 닿는 가장 이른 시각 τ. 없으면 None.

    비대칭 설계다(기획 6장). AMR 은 **관측된 속도**로 앞으로 쓸고, 사람은 어느 방향으로든
    갈 수 있다고 보아 반경이 시간에 비례해 커진다:

        |p_amr + v_amr·τ − p_h|  ≤  radius + growth·τ
        radius = r_amr + r_h + σ,  growth = v_h,max

    사람의 관측 속도를 쓰지 않는 이유: 사람은 한 걸음에 방향을 바꾸고, 그 속도 추정을
    믿고 '안전하다' 고 판정하면 틀렸을 때의 대가가 크다. AMR 은 궤도가 안정적이라 믿는다.

    부등식을 τ 에 대해 제곱해 이차식으로 푼다(격자 탐색보다 정확하고 싸다):
        A τ² + B τ + C ≤ 0
        A = |v|² − growth²,  B = 2(Δ·v − radius·growth),  C = |Δ|² − radius²
    """
    dx, dy = p_amr[0] - p_h[0], p_amr[1] - p_h[1]
    vx, vy = v_amr
    A = vx * vx + vy * vy - growth * growth
    B = 2.0 * ((dx * vx + dy * vy) - radius * growth)
    C = dx * dx + dy * dy - radius * radius

    if C <= 0.0:                      # 지금 이미 겹쳐 있다
        return 0.0

    if abs(A) < 1e-12:
        if B >= 0.0:                  # 멀어지기만 한다
            return None
        tau = -C / B
        return tau if 0.0 <= tau <= horizon else None

    disc = B * B - 4.0 * A * C
    if disc < 0.0:
        # A>0(AMR 이 사람의 최대 확장보다 빠름)인데 실근이 없으면 영영 안 닿는다.
        # A<0 이면 C>0 인 이상 판별식이 음수일 수 없으므로 여기 오지 않는다.
        return None
    sq = math.sqrt(disc)
    r1, r2 = sorted(((-B - sq) / (2.0 * A), (-B + sq) / (2.0 * A)))

    if A > 0.0:
        # 위로 열린 포물선 — 두 근 사이에서만 성립한다. C>0 이라 τ=0 은 구간 밖이다.
        tau = r1
    else:
        # 아래로 열린 포물선 — 근 바깥에서 성립한다. τ=0 이 구간 안(값이 C>0)이므로
        # 큰 근을 넘어서야 닿는다. 사람이 언젠가는 걸어와 닿는다는 뜻이라 보통 멀다.
        tau = r2
    if tau < 0.0 or tau > horizon:
        return None
    return tau


def closest_approach(dp: tuple[float, float],
                     dv: tuple[float, float]) -> tuple[float, float]:
    """등속 가정에서 최근접 시각과 그때의 거리. -> (t_cpa, d_cpa)

    TTC 보조 특징량이다(기획 5.4). 멀어지는 중이면 t=0 이고 지금 거리가 최근접이다.
    """
    dvv = dv[0] * dv[0] + dv[1] * dv[1]
    if dvv < 1e-12:
        return (0.0, math.hypot(dp[0], dp[1]))
    t = -(dp[0] * dv[0] + dp[1] * dv[1]) / dvv
    if t < 0.0:
        return (0.0, math.hypot(dp[0], dp[1]))
    return (t, math.hypot(dp[0] + dv[0] * t, dp[1] + dv[1] * t))

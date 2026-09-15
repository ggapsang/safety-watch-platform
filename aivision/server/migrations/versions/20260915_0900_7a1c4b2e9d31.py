"""카메라별 녹화 용량 상한과 요일·시간대 스케줄

**용량 상한을 카메라별로도 둔다.** 지금까지는 전체 하나뿐이었다. 그 이유는 디스크가
하나여서, 카메라마다 상한을 주면 합이 디스크를 넘을 수 있어 정작 막고 싶었던 사고를
못 막는다는 것이었다. 그 걱정은 여전히 맞다 — 그래서 전체 상한을 **없애지 않는다.**
카메라별로 먼저 깎고, 그래도 전체가 넘으면 전체 기준으로 한 번 더 깎는다. 카메라별
상한은 '한 대가 디스크를 독차지하는 것' 을 막는 층이고, 전체 상한은 마지막 방어선이다.

**요일·시간대 자동 녹화.** 근무 시간에만 녹화하면 용량이 크게 준다. 미디어 서버는
이런 스케줄을 모르므로 코어가 때가 되면 녹화를 켜고 끈다.

시각은 현장 시간대(TZ, 기본 Asia/Seoul)로 읽는다. 현장 사람이 벽시계를 보고 적는
값이라 UTC 로 두면 적을 때마다 환산해야 하고, 서머타임이 없는 한국에서도 서버를
UTC 로 띄우는 순간 9시간이 어긋난다.

기본값은 지금 동작 그대로다 — 상한 0(제한 없음), 스케줄 없음(항상 녹화). 이미 돌고
있는 현장이 이 마이그레이션으로 동작이 바뀌어서는 안 된다.

Revision ID: 7a1c4b2e9d31
Revises: c1d2e3f4a5b6
Create Date: 2026-09-15 09:00:00.000000+09:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '7a1c4b2e9d31'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cameras", sa.Column("record_max_gb", sa.Float(),
                                       nullable=False, server_default="0"))
    op.add_column("cameras", sa.Column("record_schedule", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("cameras", "record_schedule")
    op.drop_column("cameras", "record_max_gb")

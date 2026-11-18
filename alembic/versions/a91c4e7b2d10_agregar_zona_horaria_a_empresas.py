"""agregar zona horaria a empresas

Revision ID: a91c4e7b2d10
Revises: 874f3bd80042
Create Date: 2026-11-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a91c4e7b2d10'
down_revision: Union[str, Sequence[str], None] = '874f3bd80042'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'empresas',
        sa.Column('zona_horaria', sa.String(length=50), server_default='America/La_Paz', nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('empresas', 'zona_horaria')

"""agregar generada_por_ia a recomendaciones

Revision ID: d7e3a1f5c820
Revises: b4d8f2a6c931
Create Date: 2026-11-15 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7e3a1f5c820'
down_revision: Union[str, Sequence[str], None] = 'b4d8f2a6c931'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Las recomendaciones existentes son todas de plantilla
    op.add_column(
        'recomendaciones',
        sa.Column('generada_por_ia', sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('recomendaciones', 'generada_por_ia')

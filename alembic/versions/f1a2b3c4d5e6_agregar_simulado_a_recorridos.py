"""agregar simulado a recorridos (simulador de demo del modo pruebas)

Separada de las demás para poder revertirla sola al retirar el modo pruebas.

Revision ID: f1a2b3c4d5e6
Revises: e3b7c9a1f402
Create Date: 2026-11-20 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e3b7c9a1f402'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'recorridos',
        sa.Column('simulado', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('recorridos', 'simulado')

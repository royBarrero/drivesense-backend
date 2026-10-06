"""crear tabla validaciones_eventos (temporal: modo pruebas)

Revision ID: 5e7a9c3d1b20
Revises: 14b1806c768d
Create Date: 2026-10-06 16:58:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5e7a9c3d1b20'
down_revision: Union[str, Sequence[str], None] = '14b1806c768d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('validaciones_eventos',
    sa.Column('evento_id', sa.Integer(), nullable=False),
    sa.Column('resultado', sa.Enum('correcto', 'falso', name='resultado_validacion'), nullable=False),
    sa.Column('fecha', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['evento_id'], ['eventos.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('evento_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('validaciones_eventos')
    # Autogenerate no elimina los enums nativos
    sa.Enum(name='resultado_validacion').drop(op.get_bind(), checkfirst=True)

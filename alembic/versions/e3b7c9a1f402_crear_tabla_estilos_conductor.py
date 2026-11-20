"""crear tabla estilos_conductor

Revision ID: e3b7c9a1f402
Revises: a6776e17fbb9
Create Date: 2026-11-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e3b7c9a1f402'
down_revision: Union[str, Sequence[str], None] = 'a6776e17fbb9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ESTILOS = (
    'prudente', 'intermedio', 'brusco_curvas', 'acelerador', 'brusco_frenar', 'veloz', 'sin_datos'
)


def upgrade() -> None:
    """Upgrade schema."""
    # El enum se crea una vez: lo usan dos columnas
    postgresql.ENUM(*ESTILOS, name='estilo_conduccion').create(op.get_bind())
    estilo = postgresql.ENUM(*ESTILOS, name='estilo_conduccion', create_type=False)
    op.create_table(
        'estilos_conductor',
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('estilo', estilo, nullable=False),
        sa.Column('fecha_estilo', sa.DateTime(timezone=True), nullable=False),
        sa.Column('estilo_anterior', estilo, nullable=True),
        sa.Column('fecha_alerta', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fecha_actualizacion', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ),
        sa.PrimaryKeyConstraint('usuario_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('estilos_conductor')
    op.execute('DROP TYPE estilo_conduccion')

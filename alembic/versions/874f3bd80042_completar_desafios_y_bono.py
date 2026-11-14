"""completar desafios y bono

Revision ID: 874f3bd80042
Revises: 67346f20d44d
Create Date: 2026-11-14 00:03:30.954644

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '874f3bd80042'
down_revision: Union[str, Sequence[str], None] = '67346f20d44d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Los valores nuevos del enum no se pueden usar dentro de la misma transacción
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE fuente_experiencia ADD VALUE IF NOT EXISTS 'desafio'")
        op.execute("ALTER TYPE fuente_experiencia ADD VALUE IF NOT EXISTS 'bono_semana'")
    op.add_column('desafios_semanales', sa.Column('fecha_completado', sa.DateTime(timezone=True), nullable=True))
    op.add_column('experiencias', sa.Column('desafio_id', sa.Integer(), nullable=True))
    op.create_unique_constraint('uq_experiencias_desafio_fuente', 'experiencias', ['desafio_id', 'fuente'])
    op.create_foreign_key('fk_experiencias_desafio_id', 'experiencias', 'desafios_semanales', ['desafio_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_experiencias_desafio_id', 'experiencias', type_='foreignkey')
    op.drop_constraint('uq_experiencias_desafio_fuente', 'experiencias', type_='unique')
    op.drop_column('experiencias', 'desafio_id')
    op.drop_column('desafios_semanales', 'fecha_completado')
    # Postgres no quita valores de un enum: se borran las filas con las fuentes nuevas
    # (los puntos de desafíos y bonos) y se recrea el tipo solo con 'recorrido'
    op.execute("DELETE FROM experiencias WHERE fuente IN ('desafio', 'bono_semana')")
    op.execute("ALTER TYPE fuente_experiencia RENAME TO fuente_experiencia_anterior")
    op.execute("CREATE TYPE fuente_experiencia AS ENUM ('recorrido')")
    op.execute(
        "ALTER TABLE experiencias ALTER COLUMN fuente TYPE fuente_experiencia "
        "USING fuente::text::fuente_experiencia"
    )
    op.execute("DROP TYPE fuente_experiencia_anterior")

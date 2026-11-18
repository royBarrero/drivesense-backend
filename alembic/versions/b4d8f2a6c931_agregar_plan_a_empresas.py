"""agregar plan de suscripcion a empresas

Revision ID: b4d8f2a6c931
Revises: a91c4e7b2d10
Create Date: 2026-11-15 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b4d8f2a6c931'
down_revision: Union[str, Sequence[str], None] = 'a91c4e7b2d10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

plan_suscripcion = postgresql.ENUM('gratis', 'premium', 'empresas', name='plan_suscripcion', create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    plan_suscripcion.create(op.get_bind(), checkfirst=True)
    op.add_column('empresas', sa.Column('plan', plan_suscripcion, server_default='gratis', nullable=False))
    op.add_column('empresas', sa.Column('plan_inicio', sa.DateTime(timezone=True), nullable=True))
    op.add_column('empresas', sa.Column('plan_vencimiento', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('empresas', 'plan_vencimiento')
    op.drop_column('empresas', 'plan_inicio')
    op.drop_column('empresas', 'plan')
    plan_suscripcion.drop(op.get_bind(), checkfirst=True)

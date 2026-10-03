"""hand-added recipe items and drink finish steps

Recipe items with parts NULL are added by the guest after the pour (a dash of bitters);
drinks get a `finish` step (e.g. "Shake with ice and strain").

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01
"""
import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('drink', schema=None) as batch_op:
        batch_op.add_column(sa.Column('finish', sa.Text(), nullable=False, server_default=''))

    with op.batch_alter_table('recipe_item', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_recipe_item_parts_positive'), type_='check')
        batch_op.alter_column('parts', existing_type=sa.FLOAT(), nullable=True)
        batch_op.create_check_constraint(op.f('ck_recipe_item_parts_positive'), 'parts IS NULL OR parts > 0')


def downgrade() -> None:
    op.execute("DELETE FROM recipe_item WHERE parts IS NULL")
    with op.batch_alter_table('recipe_item', schema=None) as batch_op:
        batch_op.drop_constraint(op.f('ck_recipe_item_parts_positive'), type_='check')
        batch_op.alter_column('parts', existing_type=sa.FLOAT(), nullable=False)
        batch_op.create_check_constraint(op.f('ck_recipe_item_parts_positive'), 'parts > 0')

    with op.batch_alter_table('drink', schema=None) as batch_op:
        batch_op.drop_column('finish')

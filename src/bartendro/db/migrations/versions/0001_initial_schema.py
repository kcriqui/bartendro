"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-01 15:59:14.521334
"""
import sqlalchemy as sa
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('drink',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('sort_name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('popular', sa.Boolean(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('size_ml', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_drink'))
    )
    op.create_table('ingredient',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('brand', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('abv', sa.Float(), nullable=False),
    sa.Column('kind', sa.Enum('other', 'alcohol', 'tart', 'sweet', name='kind', native_enum=False, length=10), nullable=False),
    sa.Column('manual', sa.Boolean(), nullable=False),
    sa.Column('generic_id', sa.Integer(), nullable=True),
    sa.Column('generic_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['generic_id'], ['ingredient.id'], name=op.f('fk_ingredient_generic_id_ingredient')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ingredient')),
    sa.UniqueConstraint('name', name=op.f('uq_ingredient_name'))
    )
    op.create_table('option',
    sa.Column('key', sa.String(length=50), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_option'))
    )
    op.create_table('dispenser',
    sa.Column('number', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('ingredient_id', sa.Integer(), nullable=True),
    sa.Column('level', sa.Enum('unknown', 'ok', 'low', 'out', name='level', native_enum=False, length=10), nullable=False),
    sa.Column('ticks_per_ml', sa.Float(), nullable=True),
    sa.CheckConstraint('number BETWEEN 1 AND 15', name=op.f('ck_dispenser_number_range')),
    sa.ForeignKeyConstraint(['ingredient_id'], ['ingredient.id'], name=op.f('fk_dispenser_ingredient_id_ingredient')),
    sa.PrimaryKeyConstraint('number', name=op.f('pk_dispenser'))
    )
    op.create_table('pour_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('time', sa.DateTime(), nullable=False),
    sa.Column('drink_id', sa.Integer(), nullable=True),
    sa.Column('ingredient_id', sa.Integer(), nullable=True),
    sa.Column('size_ml', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['drink_id'], ['drink.id'], name=op.f('fk_pour_log_drink_id_drink'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['ingredient_id'], ['ingredient.id'], name=op.f('fk_pour_log_ingredient_id_ingredient')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pour_log'))
    )
    with op.batch_alter_table('pour_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pour_log_time'), ['time'], unique=False)

    op.create_table('recipe_item',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('drink_id', sa.Integer(), nullable=False),
    sa.Column('ingredient_id', sa.Integer(), nullable=False),
    sa.Column('parts', sa.Float(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.CheckConstraint('parts > 0', name=op.f('ck_recipe_item_parts_positive')),
    sa.ForeignKeyConstraint(['drink_id'], ['drink.id'], name=op.f('fk_recipe_item_drink_id_drink'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ingredient_id'], ['ingredient.id'], name=op.f('fk_recipe_item_ingredient_id_ingredient')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_recipe_item')),
    sa.UniqueConstraint('drink_id', 'ingredient_id', name=op.f('uq_recipe_item_drink_id'))
    )
    with op.batch_alter_table('recipe_item', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_recipe_item_drink_id'), ['drink_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('recipe_item', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_recipe_item_drink_id'))

    op.drop_table('recipe_item')
    with op.batch_alter_table('pour_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pour_log_time'))

    op.drop_table('pour_log')
    op.drop_table('dispenser')
    op.drop_table('option')
    op.drop_table('ingredient')
    op.drop_table('drink')

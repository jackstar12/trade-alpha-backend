"""add init_balance to trade

Revision ID: 87a393c2b228
Revises: 18b09e8ab0a2
Create Date: 2022-06-12 15:20:28.619706

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '87a393c2b228'
down_revision = '18b09e8ab0a2'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('trade', sa.Column('init_balance_id', sa.Integer(), nullable=True))
    op.create_foreign_key(None, 'trade', 'balance', ['init_balance_id'], ['id'], ondelete='SET NULL')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, 'trade', type_='foreignkey')
    op.drop_column('trade', 'init_balance_id')
    # ### end Alembic commands ###

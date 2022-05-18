"""change balance

Revision ID: 73fe1ded30a9
Revises: 8aab3af36e9b
Create Date: 2022-05-18 21:26:01.062880

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '73fe1ded30a9'
down_revision = '8aab3af36e9b'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('balance', sa.Column('realized', sa.Numeric(), nullable=False))
    op.add_column('balance', sa.Column('unrealized', sa.Numeric(), nullable=False))
    op.add_column('balance', sa.Column('total_transfered', sa.Numeric(), nullable=False))
    op.drop_index('ix_balance_time', table_name='balance')
    op.drop_column('balance', 'amount')
    op.drop_constraint('transfer_execution_id_fkey', 'transfer', type_='foreignkey')
    op.create_foreign_key(None, 'transfer', 'execution', ['execution_id'], ['id'], ondelete='CASCADE')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, 'transfer', type_='foreignkey')
    op.create_foreign_key('transfer_execution_id_fkey', 'transfer', 'execution', ['execution_id'], ['id'])
    op.add_column('balance', sa.Column('amount', sa.NUMERIC(), autoincrement=False, nullable=False))
    op.create_index('ix_balance_time', 'balance', ['time'], unique=False)
    op.drop_column('balance', 'total_transfered')
    op.drop_column('balance', 'unrealized')
    op.drop_column('balance', 'realized')
    # ### end Alembic commands ###

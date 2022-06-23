"""change balance

Revision ID: d1630e1fc83f
Revises: 73fe1ded30a9
Create Date: 2022-05-18 21:43:37.095156

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'd1630e1fc83f'
down_revision = '73fe1ded30a9'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('ix_realizedbalance_time', table_name='realizedbalance')
    op.create_index(op.f('ix_balance_time'), 'balance', ['time'], unique=False)
    op.drop_constraint('client_currently_realized_id_fkey', 'client', type_='foreignkey')
    op.create_foreign_key(None, 'client', 'balance', ['currently_realized_id'], ['id'], ondelete='SET NULL')
    op.drop_table('realizedbalance')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, 'client', type_='foreignkey')
    op.create_foreign_key('client_currently_realized_id_fkey', 'client', 'realizedbalance', ['currently_realized_id'], ['id'], ondelete='SET NULL')
    op.drop_index(op.f('ix_balance_time'), table_name='balance')
    op.create_table('realizedbalance',
    sa.Column('amount', sa.NUMERIC(), autoincrement=False, nullable=False),
    sa.Column('time', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('extra_currencies', postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('client_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], name='realizedbalance_client_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='realizedbalance_pkey')
    )
    op.create_index('ix_realizedbalance_time', 'realizedbalance', ['time'], unique=False)
    # ### end Alembic commands ###

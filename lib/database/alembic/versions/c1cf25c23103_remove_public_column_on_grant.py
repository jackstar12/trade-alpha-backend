"""remove public column on grant

Revision ID: c1cf25c23103
Revises: cd3ea0fe29c6
Create Date: 2022-11-12 16:09:12.228360

"""
import fastapi_users_db_sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c1cf25c23103'
down_revision = 'cd3ea0fe29c6'
branch_labels = None
depends_on = None


def upgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('authgrant', 'public')
    op.alter_column('journal', 'chapter_interval',
               existing_type=postgresql.INTERVAL(),
               type_=sa.Enum('DAY', 'WEEK', 'MONTH', name='intervaltype'),
               existing_nullable=True)
    # ### end Alembic commands ###


def downgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('journal', 'chapter_interval',
               existing_type=sa.Enum('DAY', 'WEEK', 'MONTH', name='intervaltype'),
               type_=postgresql.INTERVAL(),
               existing_nullable=True)
    op.add_column('authgrant', sa.Column('public', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=True))
    # ### end Alembic commands ###

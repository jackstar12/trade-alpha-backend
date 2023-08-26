"""remove archive table

Revision ID: 439d8fa64429
Revises: c75fd142c203
Create Date: 2022-10-20 14:18:57.758063

"""
import fastapi_users_db_sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '439d8fa64429'
down_revision = 'c75fd142c203'
branch_labels = None
depends_on = None


def upgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('archive')
    # ### end Alembic commands ###


def downgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('archive',
    sa.Column('event_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('registrations', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('leaderboard', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('summary', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('history_path', sa.VARCHAR(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['event_id'], ['event.id'], name='archive_event_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('event_id', name='archive_pkey')
    )
    # ### end Alembic commands ###
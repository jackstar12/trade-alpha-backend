"""add close_time on trade

Revision ID: f0698bc63edd
Revises: 1b15262ae438
Create Date: 2023-03-23 20:19:20.911971

"""

import fastapi_users_db_sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session, eagerload

from sqlalchemy.dialects import postgresql

from lib.db.models import Trade

# revision identifiers, used by Alembic.
revision = 'f0698bc63edd'
down_revision = '1b15262ae438'
branch_labels = None
depends_on = None


def upgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('trade', sa.Column('close_time', sa.DateTime(timezone=True), nullable=True))
    trades = session.query(Trade).options(eagerload(Trade.executions)).all()

    for trade in trades:
        if not trade.is_open:
            t = trade.id
            trade.close_time = trade.executions[-1].time

    session.commit()

    # ### end Alembic commands ###


def downgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('trade', 'close_time')
    # ### end Alembic commands ###

"""add subscription

Revision ID: a7ce3bd7db00
Revises: 9f0fb5d60a18
Create Date: 2022-08-15 14:08:56.402566

"""
import sqlalchemy.orm
from alembic import op
import sqlalchemy as sa
from sqlalchemy import update

import tradealpha


# revision identifiers, used by Alembic.
from tradealpha.common.dbmodels.user import User

revision = 'a7ce3bd7db00'
down_revision = '9f0fb5d60a18'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    sa.Enum('FREE', 'BASIC', 'PREMIUM', name='subscription').create(op.get_bind())
    op.add_column('user', sa.Column('subscription', sa.Enum('FREE', 'BASIC', 'PREMIUM', name='subscription'), nullable=True))
    session = sqlalchemy.orm.Session(op.get_bind())
    session.execute(
        update(User).values(subscription='BASIC')
    )
    session.commit()
    op.alter_column('user', 'subscription', nullable=False, existing_nullable=True)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('user', 'subscription')
    # ### end Alembic commands ###
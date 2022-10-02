"""add label group

Revision ID: 21a8724671d8
Revises: a2e192a59a90
Create Date: 2022-09-25 14:39:00.476165

"""
import fastapi_users_db_sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from sqlalchemy.dialects import postgresql
from tradealpha.common.dbmodels.user import User

# revision identifiers, used by Alembic.
revision = '21a8724671d8'
down_revision = None
branch_labels = None
depends_on = None


# class LabelTmp(Label):
#     user_id = sa.Column(sa.ForeignKey(User.id, ondelete="CASCADE"))


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    labelgroup = op.create_table('labelgroup',
                                 sa.Column('id', sa.Integer(), nullable=False),
                                 sa.Column('name', sa.String(), nullable=False),
                                 sa.Column('user_id', fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False),
                                 sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
                                 sa.PrimaryKeyConstraint('id')
                                 )

    session = Session(bind=op.get_bind())

    users = session.query(User).all()

    for user in users:
        session.execute(
            sa.insert(labelgroup).values({
                'name': 'General',
                'user_id': user.id
            })
        )

    op.add_column('label', sa.Column('group_id', sa.Integer(), nullable=True))

    session.execute(
        sa.update(LabelTmp).values(
            group_id=labelgroup.c.id
        ).where(
            labelgroup.c.user_id == LabelTmp.user_id
        )
    )

    op.alter_column('label', column_name='group_id', nullable=False)

    op.drop_constraint('label_user_id_fkey', 'label', type_='foreignkey')
    op.create_foreign_key(None, 'label', 'labelgroup', ['group_id'], ['id'], ondelete='CASCADE')
    op.drop_column('label', 'user_id')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('label', sa.Column('user_id', postgresql.UUID(), autoincrement=False, nullable=False))
    op.drop_constraint(None, 'label', type_='foreignkey')
    op.create_foreign_key('label_user_id_fkey', 'label', 'user', ['user_id'], ['id'], ondelete='CASCADE')
    op.drop_column('label', 'group_id')
    op.drop_table('labelgroup')
    # ### end Alembic commands ###

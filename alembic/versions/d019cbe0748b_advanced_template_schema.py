"""advanced template schema

Revision ID: d019cbe0748b
Revises: 2e2ccd52b7b1
Create Date: 2022-10-02 17:01:48.835723

"""
import fastapi_users_db_sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import postgresql
# revision identifiers, used by Alembic.
from lib.db.models.editing import Template

revision = 'd019cbe0748b'
down_revision = '2e2ccd52b7b1'
branch_labels = None
depends_on = None


enum = postgresql.ENUM('CHAPTER', 'TRADE', name='templatetype')


def upgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    enum.create(op.get_bind())
    op.add_column('template', sa.Column('type', enum, nullable=True))

    session.execute(
        sa.update(Template).values(
            type='CHAPTER'
        )
    )
    session.commit()

    op.alter_column('template', column_name='type', nullable=False, existing_nullable=True)

    op.add_column('template', sa.Column('parent_id', sa.Integer(), nullable=True))

    op.create_foreign_key(None, 'template', 'template', ['parent_id'], ['id'], ondelete='CASCADE')
    # ### end Alembic commands ###


def downgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, 'template', type_='foreignkey')
    op.drop_column('template', 'parent_id')
    op.drop_column('template', 'type')
    enum.drop(op.get_bind())
    # ### end Alembic commands ###

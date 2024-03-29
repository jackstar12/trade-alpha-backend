# inside of a "create the database" script, first create
# tables:
from lib.db.dbsync import Base, engine
from alembic.config import Config
from alembic import command


def init_db():
    Base.metadata.create_all(engine)

    # then, load the Alembic configuration and generate the
    # version table, "stamping" it with the most recent rev:
    alembic_cfg = Config("alembic.ini")
    command.stamp(alembic_cfg, "head")


if __name__ == "__main__":
    init_db()

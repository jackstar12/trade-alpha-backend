from typing import Any

from fastapi import APIRouter

from api.crudrouter import add_crud_routes, Route
from api.models.labelinfo import CreateLabel
from api.models.labelinfo import LabelInfo
from lib.db.models.label import Label as LabelDB, LabelGroup as LabelGroupDB
from lib.db.models.user import User


def label_filter(stmt: Any, user: User):
    return stmt.join(LabelDB.group).where(LabelGroupDB.user_id == user.id)


router = APIRouter(tags=["Label"], prefix="/label")

add_crud_routes(
    router,
    table=LabelDB,
    read_schema=LabelInfo,
    create_schema=CreateLabel,
    default_route=Route(add_filters=label_filter),
)

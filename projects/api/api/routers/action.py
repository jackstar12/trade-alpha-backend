from fastapi import APIRouter

from api.crudrouter import add_crud_routes
from lib.db.models.action import Action
from lib.models.action import ActionInfo, ActionCreate

router = APIRouter(tags=["action"], prefix="/action")

add_crud_routes(
    router, table=Action, read_schema=ActionInfo, create_schema=ActionCreate
)

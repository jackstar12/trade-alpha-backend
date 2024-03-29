from typing import Optional

from fastapi import Depends, APIRouter, Body
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.crudrouter import add_crud_routes, Route
from api.dependencies import get_db
from api.users import CurrentUser, DefaultGrant
from api.utils.responses import BadRequest, OK, Unauthorized
from lib.db.dbasync import wrap_greenlet, db_unique, opt_op
from lib.db.models import User
from lib.models import BaseModel
from lib.models.authgrant import AuthGrantCreate, AuthGrantInfo

from lib.db.models.authgrant import AuthGrant, AssociationType

from lib.models import InputID


router = APIRouter(tags=["Auth Grant"], prefix="/auth-grant")


class AddToGrant(BaseModel):
    pass
    # id: InputID


@router.get("/current", response_model=AuthGrantInfo)
@wrap_greenlet
def get_current_grant(grant: AuthGrant = Depends(DefaultGrant)):
    return OK(result=AuthGrantInfo.from_orm(grant))


@router.post("/permit/{type}/{id}", response_model=AuthGrantInfo)
async def add_to_grant(
    type: AssociationType,
    id: InputID,
    grant_id: Optional[InputID] = Body(default=None),
    public: Optional[bool] = Body(default=None),
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    # Verify grant
    grant = await db_unique(
        select(AuthGrant).where(
            AuthGrant.user_id == user.id,
            opt_op(AuthGrant.id, grant_id),
            opt_op(AuthGrant.public, public),
        ),
        session=db,
    )

    if not grant:
        if public is not None:
            grant = AuthGrant(public=public, user=user)
        else:
            raise Unauthorized("Can not access this grant")

    impl = type.get_impl()
    if not impl:
        raise BadRequest(detail="Invalid Type")

    association = impl()
    association.grant = grant
    association.identity = id

    try:
        db.add(association)
        await db.commit()
    except IntegrityError:
        raise BadRequest(f"Invalid grant or {type.value} id")

    return AuthGrantInfo.from_orm(grant)


@router.delete("/permit/{type}/{id}")
async def forbid_grant(
    type: AssociationType,
    id: InputID,
    grant_id: Optional[InputID] = Body(default=None),
    public: Optional[bool] = Body(default=None),
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    impl = type.get_impl()
    if not impl:
        raise BadRequest(detail="Invalid Type")

    stmt = (
        delete(impl)
        .where(
            impl.identity == id,
            AuthGrant.id == impl.grant_id,
            AuthGrant.user_id == user.id,
            opt_op(impl.grant_id, grant_id),
            opt_op(AuthGrant.public, public),
        )
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    await db.commit()

    if result.rowcount == 1:
        return OK()
    else:
        raise BadRequest(f"Invalid grant or {type.value} id")


add_crud_routes(
    router,
    table=AuthGrant,
    read_schema=AuthGrantInfo,
    create_schema=AuthGrantCreate,
    default_route=Route(eager_loads=[AuthGrant.user]),
)

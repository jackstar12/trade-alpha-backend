from datetime import datetime
from enum import Enum
from operator import and_
from typing import Optional, Type, Literal

from fastapi import Depends, APIRouter
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.crudrouter import add_crud_routes, Route
from api.dependencies import get_db
from api.users import CurrentUser, DefaultGrant
from api.utils.responses import BadRequest, OK
from database.dbasync import wrap_greenlet
from database.dbmodels import User
from database.dbsync import BaseMixin
from database.models import OrmBaseModel, OutputID, CreateableModel, BaseModel
from database.models.user import UserPublicInfo

from database.dbmodels.authgrant import (
    AuthGrant,
    JournalGrant,
    ChapterGrant,
    EventGrant,
    TradeGrant,
    GrantAssociaton, DiscordPermission, AssociationType
)
from database.redis import TableNames

from lib.database.database.models import InputID


class AuthGrantCreate(CreateableModel):
    expires: Optional[datetime]
    public: Optional[bool]
    discord: Optional[DiscordPermission]
    token: Optional[bool]
    wildcards: Optional[list[AssociationType]]

    def dict(
            self,
            *,
            exclude_none=False,
            **kwargs
    ):
        return super().dict(exclude_none=False, **kwargs)

    def get(self, user: User) -> AuthGrant:
        return AuthGrant(**self.dict(), user=user)


class AuthGrantInfo(AuthGrantCreate, OrmBaseModel):
    id: OutputID
    owner: UserPublicInfo
    token: Optional[str]


router = APIRouter(
    tags=["Auth Grant"],
    prefix="/auth-grant"
)


class AddToGrant(BaseModel):
    pass
    # id: InputID


@router.get('/current', response_model=AuthGrantInfo)
@wrap_greenlet
def get_current_grant(grant: AuthGrant = Depends(DefaultGrant)):
    return OK(result=AuthGrantInfo.from_orm(grant))


@router.post('/{grant_id}/permit/{type}/{id}')
async def add_to_grant(grant_id: int,
                       type: AssociationType,
                       id: int,
                       user: User = Depends(CurrentUser),
                       db: AsyncSession = Depends(get_db)):
    impl = type.get_impl()
    if not impl:
        raise BadRequest(detail='Invalid Type')
    instance = impl()

    instance.grant_id = grant_id
    instance.user = user
    instance.identity = id

    try:
        db.add(instance)
        await db.commit()
    except IntegrityError as e:
        raise BadRequest(f'Invalid grant or {type.value} id')

    return OK()


@router.delete('/{grant_id}/permit/{type}/{id}')
async def add_to_grant(grant_id: int,
                       type: AssociationType,
                       id: int,
                       user: User = Depends(CurrentUser),
                       db: AsyncSession = Depends(get_db)):
    impl = type.get_impl()
    if not impl:
        raise BadRequest(detail='Invalid Type')
    stmt = delete(impl).where(
        impl.identity == id,
        impl.grant_id == grant_id,
        AuthGrant.id == grant_id,
        AuthGrant.user_id == user.id
    ).execution_options(
        synchronize_session=False
    )
    result = await db.execute(
        stmt
    )
    await db.commit()

    if result.rowcount == 1:
        return OK()
    else:
        raise BadRequest(f'Invalid grant or {type.value} id')


add_crud_routes(router,
                table=AuthGrant,
                read_schema=AuthGrantInfo,
                create_schema=AuthGrantCreate,
                default_route=Route(
                    eager_loads=[AuthGrant.user]
                ))

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTasks

from api.dependencies import get_db
from api.models.user import UserRead
from lib.models.user import UserPublicInfo
from api.users import CurrentUser
from api.users import get_current_user
from api.utils.responses import OK, ResponseModel
from lib.db.dbasync import redis
from lib.db.models.user import User
from lib.models.user import ProfileData, UserProfile
from api.models.alert import Alert
from api.models.client import ClientInfo
from lib.models import BaseModel
from lib.models.document import DocumentModel

router = APIRouter(
    tags=["transfer"],
    responses={
        401: {"detail": "Wrong Email or Password"},
        400: {"detail": "Email is already used"},
    },
    prefix="/user",
)


@router.delete("")
async def delete_user(
    db: AsyncSession = Depends(get_db), user: User = Depends(CurrentUser)
):
    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()

    return OK("Success")


user_info_dep = get_current_user(User.oauth_accounts, User.all_clients)


class UserInfo(UserPublicInfo, UserRead):
    all_clients: list[ClientInfo]
    alerts: list[Alert]

    class Config:
        orm_mode = True


# @router.get('/profile', response_model=ResponseModel[UserPublicInfo])
# async def info(user: User = Depends(user_info_dep),
#               db: AsyncSession = Depends(get_db)):
#    for account in user.oauth_accounts:
#        await account.populate_oauth_data(redis=redis)
#
#    await db.commit()
#
#    return OK(
#        result=UserInfo.from_orm(user)
#    )


@router.get("", response_model=ResponseModel[UserInfo])
async def info(
    background: BackgroundTasks,
    user: User = Depends(user_info_dep),
    db: AsyncSession = Depends(get_db),
):
    for account in user.oauth_accounts:
        # background.add_task(account.populate_oauth_data, redis=redis)
        await account.populate_oauth_data(redis=redis)

    await db.commit()

    return OK(
        result=UserInfo.from_orm(user),
    )


class UserUpdate(BaseModel):
    profile: Optional[UserProfile]
    about_me: Optional[DocumentModel]


@router.patch("", response_model=ResponseModel[UserPublicInfo])
async def update_user(
    body: UserUpdate,
    user: User = Depends(user_info_dep),
    db: AsyncSession = Depends(get_db),
):
    if body.profile:
        if not body.profile["src"]:
            user.info = ProfileData(
                name=body.profile["name"], avatar_url=body.profile["avatar_url"]
            )
        elif user.get_oauth(body.profile["src"]):
            user.info = body.profile["src"]

    if body.about_me:
        user.about_me = body.about_me

    await db.commit()

    return OK(result=UserPublicInfo.from_orm(user))

from http import HTTPStatus
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.dialects.postgresql import insert as insertpg
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db, get_dc_rpc_client
from lib.models import InputID
from api.models.discord_user import DiscordUserInfo
from api.users import CurrentUser
from api.utils.client import get_user_client
from api.utils.responses import OK, BadRequest, ResponseModel, InternalError
from lib.db.dbasync import db_select_all, db_select
from lib.db.models import GuildAssociation as GuildAssociationDB
from lib.db.models.discord.guild import Guild as GuildDB
from lib.db.models.user import User
from lib.models import BaseModel
from lib.models.discord.guild import (
    UserRequest,
    Guild as GuildModel,
    GuildRequest,
    GuildData,
)
from lib.db.redis import rpc
from lib.utils import groupby_unique

router = APIRouter(tags=["discord"])


@router.get(
    "/discord",
    response_model=ResponseModel[DiscordUserInfo],
    description="Returns information regarding the connected Discord Account",
)
async def get_discord_info(
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
    dc_rpc: rpc.Client = Depends(get_dc_rpc_client),
):
    if not user.discord:
        raise BadRequest("No discord account connected")
    try:
        data_by_id = groupby_unique(
            await dc_rpc("guilds", UserRequest(user_id=466706956158107649)),
            lambda g: int(g["id"]),
        )

        discord_info = await user.discord.populate_oauth_data(dc_rpc.redis)
    except rpc.TimeoutError:
        raise HTTPException(
            status_code=HTTPStatus.REQUEST_TIMEOUT,
            detail="Discord data is currently not available",
        )

    guilds = await db_select_all(
        GuildDB,
        GuildDB.id.in_(data_by_id.keys()),
        eager=[GuildDB.events],
        session=db,
    )

    return OK(
        result=DiscordUserInfo(
            data=discord_info,
            guilds=[
                GuildModel.from_association(
                    data=data_by_id[guild.id],
                    guild=guild,
                    association=await db.get(
                        GuildAssociationDB, (user.discord.account_id, guild.id)
                    ),
                )
                for guild in guilds
            ],
        )
    )


@router.get("/discord/guild/{guild_id}", response_model=ResponseModel[DiscordUserInfo])
async def get_guild_info(
    guild_id: int,
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
    dc_rpc: rpc.Client = Depends(get_dc_rpc_client),
):
    if not user.discord:
        raise BadRequest("No discord account connected")

    data = await dc_rpc(
        "guild", GuildRequest(user_id=user.discord.account_id, guild_id=guild_id)
    )

    GA = GuildAssociationDB
    association = await db_select(
        GA,
        GA.guild_id == int(data["id"]),
        GA.discord_user_id == user.discord.account_id,
        eager=[GuildAssociationDB.guild],
        session=db,
    )

    return OK(result=GuildModel.from_association(data, association.guild, association))


@router.get("/discord/guild/{guild_id}/data", response_model=ResponseModel[GuildData])
async def get_guild_data(
    guild_id: int,
    user: User = Depends(CurrentUser),
    dc_rpc: rpc.Client = Depends(get_dc_rpc_client),
):
    if not user.discord:
        raise BadRequest("No discord account connected")

    data = await dc_rpc(
        "guild", GuildRequest(user_id=user.discord.account_id, guild_id=guild_id)
    )
    return OK(result=data)


class GuildUpdate(BaseModel):
    client_id: Optional[InputID]


@router.patch(
    "/discord/guild/{guild_id}", response_model=ResponseModel[DiscordUserInfo]
)
async def update_guild(
    guild_id: int,
    body: GuildUpdate,
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
    dc_rpc: rpc.Client = Depends(get_dc_rpc_client),
):
    if not user.discord:
        raise BadRequest("No discord account connected")
    try:
        data = await dc_rpc.call(
            "guild", GuildRequest(user_id=user.discord.account_id, guild_id=guild_id)
        )
    except rpc.BadRequest:
        raise BadRequest("Invalid guild id")

    if body.client_id:
        client = await get_user_client(user=user, client_id=body.client_id, db=db)

        if not client:
            raise BadRequest("Invalid client id")

        result = await db.execute(
            insertpg(GuildAssociationDB)
            .values(
                guild_id=int(data["id"]),
                client_id=client.id,
                discord_user_id=user.discord.account_id,
            )
            .on_conflict_do_update(
                index_elements=[
                    GuildAssociationDB.guild_id,
                    GuildAssociationDB.discord_user_id,
                ],
                set_={GuildAssociationDB.client_id: client.id},
            )
        )
        if result.rowcount != 1:
            raise InternalError("Could not update")

    await db.commit()
    return OK("Updated")

from fastapi import APIRouter, Depends, Body
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_messenger, get_db
from api.users import CurrentUser
from api.models.transfer import Transfer
from api.utils.responses import OK, NotFound
from lib.db.dbasync import db_exec, db_first
from lib.db.models.client import Client, add_client_checks
from lib.db.models.transfer import Transfer as TransferDB
from lib.db.models.user import User

router = APIRouter(
    tags=["transfer"],
    dependencies=[Depends(CurrentUser), Depends(get_messenger)],
    responses={
        401: {"detail": "Wrong Email or Password"},
        400: {"detail": "Email is already used"},
    },
)


@router.get("/{transfer_id}")
async def get_transfer(transfer_id: int, user: User = Depends(CurrentUser)):
    transfer = await db_first(
        add_client_checks(
            select(TransferDB).filter_by(id=transfer_id).join(TransferDB.client),
            user_id=user.id,
        )
    )

    if transfer:
        return Transfer.from_orm(transfer)
    else:
        raise NotFound("Invalid transfer_id")


@router.patch("/{transfer_id}")
async def update_transfer(
    transfer_id: int,
    note: str = Body(...),
    user: User = Depends(CurrentUser),
    db_session: AsyncSession = Depends(get_db),
):
    await db_exec(
        update(TransferDB)
        .where(TransferDB.id == transfer_id)
        .where(TransferDB.client_id == Client.id)
        .where(Client.user_id == user.id)
        .values(note=note),
        session=db_session,
    )

    await db_session.commit()

    return OK("Updated")

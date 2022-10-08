from typing import Any

from fastapi import Depends
from sqlalchemy import select, insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.crudrouter import create_crud_router
from database.dbmodels import TradeDB
from database.dbasync import db_first, db_select, db_exec
from api.dependencies import get_db
from api.users import CurrentUser

from database.dbmodels.client import add_client_filters
from database.dbmodels.label import Label as LabelDB, LabelGroup as LabelGroupDB
from api.models.labelinfo import LabelInfo, LabelGroupInfo, LabelGroupCreate, RemoveLabel, AddLabel, CreateLabel
from database.dbmodels.trade import Trade, trade_association
from database.dbmodels.user import User
from api.utils.responses import BadRequest, OK, NotFound


def label_filter(stmt: Any, user: User):
    return stmt.join(
        LabelDB.group
    ).where(
        LabelGroupDB.user_id == user.id
    )


router = create_crud_router(prefix="/label",
                            table=LabelDB,
                            read_schema=LabelInfo,
                            create_schema=CreateLabel,
                            add_filters=label_filter)

group_router = create_crud_router(prefix="/group",
                                  table=LabelGroupDB,
                                  read_schema=LabelGroupInfo,
                                  create_schema=LabelGroupCreate,
                                  eager_loads=[LabelGroupDB.labels])

router.include_router(group_router)


def add_trade_filters(stmt, user: User, trade_id: int):
    return add_client_filters(
        stmt.filter(
            Trade.id == trade_id,
        ).join(Trade.client),
        user
    )


@router.post('/trade')
async def add_label(body: AddLabel,
                    user: User = Depends(CurrentUser),
                    db: AsyncSession = Depends(get_db)):
    verify_trade_id = await db_exec(
        add_trade_filters(
            select(TradeDB.id),
            user=user,
            trade_id=body.trade_id
        ),
        session=db
    )

    if not verify_trade_id:
        return BadRequest('Invalid Trade ID')

    verify_label_id = await db_select(
        LabelDB,
        id=body.label_id,
        user_id=user.id,
        session=db
    )

    if not verify_label_id:
        return BadRequest('Invalid Label ID')

    await db_exec(
        insert(trade_association).values(
            trade_id=body.trade_id,
            label_id=body.label_id
        ),
        session=db
    )
    await db.commit()

    return OK('Success')


@router.delete('/trade')
async def remove_label(body: RemoveLabel,
                       user: User = Depends(CurrentUser),
                       db: AsyncSession = Depends(get_db)):
    trade = await db_first(
        add_trade_filters(select(Trade), user, body.trade_id),
        Trade.labels
    )
    label = await db_first(
        select(LabelDB).filter(
            LabelDB.id == body.label_id,
            LabelDB.client_id == body.client_id
        )
    )
    if label:
        if label in trade.labels:
            trade.labels.remove(label)
        else:
            return BadRequest('Trade already has this label')
    else:
        return NotFound('Invalid Label ID')



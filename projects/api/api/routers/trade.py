import itertools
import time
from datetime import datetime
from decimal import Decimal
from typing import List, Type, Union, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from pydantic import conlist
from sqlalchemy import select, delete, insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTasks

import api.utils.client as client_utils
import core
from api.dependencies import get_messenger, get_db, FilterQueryParamsDep
from database.models.execution import Execution
from database.models.trade import (
    Trade,
    BasicTrade,
    DetailledTrade,
    UpdateTrade,
    UpdateTradeResponse,
)
from api.routers.template import query_templates
from api.users import CurrentUser, get_auth_grant_dependency
from api.utils.responses import (
    BadRequest,
    OK,
    CustomJSONResponse,
    ResponseModel,
    Unauthorized,
)
from database.dbasync import db_first, db_all
from database.dbmodels import TradeDB as TradeDB, Chapter, Execution as ExecutionDB
from database.dbmodels.authgrant import AuthGrant, AssociationType, ChapterGrant
from database.dbmodels.client import ClientQueryParams
from database.dbmodels.client import add_client_checks
from database.dbmodels.label import Label as LabelDB
from database.dbmodels.mixins.filtermixin import FilterParam
from database.dbmodels.pnldata import PnlData
from database.dbmodels.trade import trade_association
from database.dbmodels.user import User
from database.models import BaseModel, OutputID, InputID
from database.models.document import DocumentModel
from database.redis.client import ClientCacheKeys
from database.utils import query_table

router = APIRouter(
    tags=["trade"],
    dependencies=[Depends(get_messenger)],
    responses={
        401: {"detail": "Wrong Email or Password"},
        400: {"detail": "Email is already used"},
    },
)


def add_trade_filters(stmt, user_id: UUID, trade_id: int):
    return add_client_checks(
        stmt.filter(
            TradeDB.id == trade_id,
        ).join(TradeDB.client),
        user_id,
    )


@router.patch("/trade/{trade_id}", response_model=UpdateTradeResponse)
async def update_trade(
    trade_id: InputID,
    body: UpdateTrade,
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    trade = await db_first(
        add_trade_filters(select(TradeDB), user.id, trade_id),
        TradeDB.labels,
        session=db,
    )

    if not trade:
        raise BadRequest("Invalid Trade ID")

    if body.notes:
        trade.notes = body.notes

    if body.labels:
        added = body.labels.label_ids - set(trade.label_ids)
        for label_id in added:
            await db.execute(
                insert(trade_association).values(trade_id=trade_id, label_id=label_id),
            )

        if body.labels.group_ids:
            await db.execute(
                delete(trade_association).where(
                    trade_association.c.trade_id == trade_id,
                    # trade_association.c.label_id == LabelDB.id,
                    ~trade_association.c.label_id.in_(body.labels.label_ids),
                    LabelDB.group_id.in_(body.labels.group_ids),
                    # ~LabelDB.id.in_(body.labels.label_ids),
                    # Group.user_id == User.id,
                    # User.id == user.id
                ),
            )

    if body.template_id:
        template = await query_templates(
            [body.template_id], user_id=user.id, session=db
        )
        trade.notes = DocumentModel(content=template.body, type="doc")

    await db.commit()
    return UpdateTradeResponse(
        label_ids=body.labels and trade.label_ids,
        notes=body.template_id and trade.notes,
    )


auth = get_auth_grant_dependency(ChapterGrant)


class TradeQueryParams(ClientQueryParams):
    trade_ids: set[InputID]
    from_chapter: Optional[InputID]
    from_journal: Optional[InputID]

    def within(self, other: "TradeQueryParams"):
        return (
            other and super().within(other) and self.trade_ids.issubset(other.trade_ids)
        )


def get_trade_params(
    client_id: set[InputID] = Query(default=[]),
    trade_id: set[InputID] = Query(default=[]),
    currency: str = Query(default=None),
    since: datetime = Query(default=None),
    to: datetime = Query(default=None),
    from_chapter: Optional[InputID] = Query(default=None),
    from_journal: Optional[InputID] = Query(default=None),
    order: Literal["asc", "desc"] = Query(default="asc"),
):
    return TradeQueryParams(
        client_ids=client_id,
        trade_ids=trade_id,
        currency=currency,
        since=since,
        from_chapter=from_chapter,
        from_journal=from_journal,
        to=to,
        order=order,
    )


def create_trade_endpoint(path: str, model: Type[BasicTrade], *eager, **kwargs):
    class Trades(BaseModel):
        data: list[model]

    TradeCache = client_utils.ClientCacheDependency(
        core.join_args(ClientCacheKeys.TRADE, path), Trades, auth, get_trade_params
    )

    FilterQueryParams = FilterQueryParamsDep(TradeDB, model)

    @router.get(f"/{path}", response_model=list[model], **kwargs)
    async def get_trades(
        background_tasks: BackgroundTasks,
        chapter_id: InputID = Query(default=None),
        cache: client_utils.ClientCache = Depends(TradeCache),
        query_params: TradeQueryParams = Depends(get_trade_params),
        filters: list[FilterParam] = Depends(FilterQueryParams),
        grant: AuthGrant = Depends(auth),
        db: AsyncSession = Depends(get_db),
    ):
        time.perf_counter()

        if not grant.is_root_for(AssociationType.TRADE):
            if chapter_id:
                data = await db_first(
                    Chapter.query_nodes(chapter_id, query_params=query_params),
                    session=db,
                )
                if not data:
                    raise Unauthorized()
            else:
                trade_id = await grant.check_ids(
                    AssociationType.TRADE, query_params.trade_ids
                )
                if not trade_id:
                    return OK(result=[])
        if query_params.from_chapter or query_params.from_journal:
            data = await db.execute(
                Chapter.query_nodes(
                    root_id=query_params.from_chapter,
                    journal_id=query_params.from_journal,
                ),
            )
            query_params.trade_ids = set(
                map(
                    int,
                    itertools.chain.from_iterable(
                        child["tradeIds"] for child in data if "tradeIds" in child
                    ),
                )
            )

        trades_db = await client_utils.query_trades(
            *eager,
            user_id=grant.user_id,
            query_params=query_params,
            trade_ids=query_params.trade_ids,
            filters=filters,
            db=db,
        )
        results = []
        for trade in trades_db:
            parsed = model.from_orm(trade)
            results.append(parsed)
            continue
            if all(f.check(trade) for f in filters):
                results.append(parsed)

        return OK(result=results)
        hits, misses = await cache.read(db)

        if misses:
            query_params.client_ids = misses

            trades_db = await client_utils.query_trades(
                *eager,
                user_id=grant.author_id,
                query_params=query_params,
                trade_ids=query_params.trade_ids,
                db=db,
            )

            trades_by_client = {}
            for trade_db in trades_db:
                if trade_db.client_id not in trades_by_client:
                    trades_by_client[trade_db.client_id] = Trades(data=[])
                trades_by_client[trade_db.client_id].data.append(
                    model.from_orm(trade_db)
                )
            for client_id, trades in trades_by_client.items():
                hits.append(trades)
                background_tasks.add_task(cache.write, client_id, trades)

        time.perf_counter()
        return OK(
            result=[
                trade
                for trades in hits
                for trade in trades.data
                if all(f.check(trade) for f in filters)
            ]
        )


create_trade_endpoint(
    "trade-overview",
    BasicTrade,
)
create_trade_endpoint(
    "trade",
    Trade,
    TradeDB.init_amount,
    TradeDB.labels,
)
create_trade_endpoint(
    "trade-detailled",
    DetailledTrade,
    TradeDB.executions,
    TradeDB.pnl_data,
    TradeDB.initial,
    TradeDB.max_pnl,
    TradeDB.min_pnl,
    TradeDB.labels,
    TradeDB.init_amount,
)


class PnlDataResponse(BaseModel):
    # Named Tuples are not supported (workaround with conlist)
    by_trade: dict[
        OutputID, list[conlist(Union[int, Decimal], min_items=3, max_items=3)]
    ]


@router.get("/trade-detailled/pnl-data", response_model=ResponseModel[PnlDataResponse])
async def get_pnl_data(
    trade_id: list[InputID] = Query(default=[]),
    chapter_id: InputID = Query(default=None),
    grant: AuthGrant = Depends(auth),
    db: AsyncSession = Depends(get_db),
):
    if not grant.is_root_for(AssociationType.TRADE):
        if chapter_id:
            node = await db_first(Chapter.query_nodes(chapter_id), session=db)
            if not node:
                raise Unauthorized()
        else:
            trade_id = await grant.check_ids(AssociationType.TRADE, trade_id)
            if not trade_id:
                return OK(result=[])

    data: List[PnlData] = await db_all(
        add_client_checks(
            select(PnlData)
            .where(PnlData.trade_id.in_(trade_id) if trade_id else True)
            .join(PnlData.trade)
            .join(TradeDB.client)
            .order_by(PnlData.time),
            user_id=grant.user_id,
        ),
        session=db,
    )

    result = {}
    for pnl_data in data:
        result.setdefault(str(pnl_data.trade_id), []).append(pnl_data.compact)

    return CustomJSONResponse(content={"by_trade": jsonable_encoder(result)})


ExecFilters = FilterQueryParamsDep(TradeDB, BasicTrade)


@router.get("/trades/executions", response_model=list[Execution])
async def get_executions(
    trade_id: list[InputID] = Query(default=None),
    query_params: TradeQueryParams = Depends(get_trade_params),
    filters: list[FilterParam] = Depends(ExecFilters),
    grant: AuthGrant = Depends(auth),
    db: AsyncSession = Depends(get_db),
):
    if not grant.is_root_for(AssociationType.TRADE):
        trade_id = await grant.check_ids(AssociationType.TRADE, trade_id)
        if not trade_id:
            return OK(result=[])

    stmt = select(ExecutionDB).join(ExecutionDB.trade)

    if query_params.currency:
        filters.append(FilterParam(field="settle", values=[query_params.currency]))

    for f in filters:
        stmt = f.apply(stmt, TradeDB)

    data = await query_table(
        table=ExecutionDB,
        stmt=stmt,
        user_id=grant.user.id,
        query_params=query_params,
        db=db,
        client_col=TradeDB.client,
    )

    return CustomJSONResponse(content=[Execution.from_orm(raw) for raw in data])

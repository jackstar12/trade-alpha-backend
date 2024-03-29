import itertools
import logging
import time
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

import aiohttp
import jwt
from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, select, asc, func, desc, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTasks

import api.utils.client as client_utils
from api.dependencies import get_messenger, get_db, get_http_session
from api.models.client import (
    ClientConfirm,
    ClientEdit,
    ClientOverview,
    ClientCreateBody,
    ClientInfo,
    ClientCreateResponse,
    get_query_params,
    ClientDetailed,
    ClientOverviewCache,
)
from lib.models.trade import BasicTrade
from api.settings import settings
from api.users import CurrentUser, get_auth_grant_dependency
from api.utils.responses import (
    BadRequest,
    OK,
    CustomJSONResponse,
    NotFound,
    ResponseModel,
    InternalError,
    Unauthorized,
)
from lib.exchanges import EXCHANGES
from lib.utils import validate_kwargs, groupby, date_string, sum_iter, utc_now
from lib.db.calc import create_intervals
from lib.db.dbasync import db_first, redis, async_maker, time_range, db_all, opt_eq
from lib.db.models import TradeDB, BalanceDB, Execution, Chapter
from lib.db.models.authgrant import ChapterGrant, AuthGrant
from lib.db.models.balance import Amount
from lib.db.models.client import Client, add_client_checks, ClientState, ClientRedis
from lib.db.models.client import ClientQueryParams
from lib.db.models.transfer import Transfer as TransferDB
from lib.db.models.user import User
from lib.db.enums import IntervalType
from lib.db.errors import InvalidClientError, ResponseError
from lib.models import OrmBaseModel, InputID
from lib.models.balance import Balance
from lib.models.interval import FullInterval
from lib.db.redis.client import ClientCacheKeys

router = APIRouter(
    tags=["client"],
    dependencies=[Depends(get_messenger)],
    responses={
        401: {"detail": "Wrong Email or Password"},
        400: {"detail": "Email is already used"},
    },
)


@router.post(
    "/client", response_model=ClientCreateResponse, dependencies=[Depends(CurrentUser)]
)
async def new_client(
    body: ClientCreateBody,
    http_session: aiohttp.ClientSession = Depends(get_http_session),
):
    try:
        exchange_cls = EXCHANGES[body.exchange]
        # Check if required keyword args are given
        if validate_kwargs(body.extra or {}, exchange_cls.required_extra_args):
            client = body.get()

            try:
                exchange = exchange_cls(
                    client, http_session, commit=False, db_maker=async_maker
                )
                init_balance = await exchange.get_balance()
            except InvalidClientError:
                raise BadRequest("Invalid API credentials")
            except ResponseError:
                raise InternalError()

            if init_balance.error is None:
                if init_balance.realized.is_zero():
                    raise BadRequest(
                        "You do not have any balance in your account. Please fund your account before registering."
                    )
                else:
                    payload = jsonable_encoder(body)
                    payload["api_secret"] = client.api_secret
                    payload["exp"] = init_balance.time + timedelta(minutes=5)

                    return ClientCreateResponse(
                        token=jwt.encode(
                            payload, settings.JWT_SECRET, algorithm="HS256"
                        ),
                        balance=Balance.from_orm(init_balance),
                    )
            else:
                raise BadRequest(
                    f"An error occured while getting your balance: {init_balance.error}."
                )
        else:
            logging.error(
                f"Not enough kwargs for exchange {exchange_cls.exchange} were given."
                f"\nGot: {body.extra}\nRequired: {exchange_cls.required_extra_args}"
            )
            args_readable = "\n".join(exchange_cls.required_extra_args)
            raise BadRequest(
                detail=f"Need more keyword arguments for exchange {exchange_cls.exchange}."
                f"\nRequirements:\n {args_readable}",
                code=40100,
            )
    except KeyError:
        raise BadRequest(f"Exchange {body.exchange} unknown")


@router.post("/client/confirm", response_model=ClientInfo)
async def confirm_client(
    body: ClientConfirm,
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    try:
        client_data = ClientCreateBody(
            **jwt.decode(body.token, settings.JWT_SECRET, algorithms=["HS256"])
        )
        client = client_data.get(user)

        db.add(client)
        await db.commit()
    except jwt.ExpiredSignatureError:
        raise BadRequest(detail="Token expired")
    except (jwt.InvalidTokenError, TypeError):
        raise BadRequest(detail="Invalid token")
    except IntegrityError:
        raise BadRequest(detail="This api key is already in use.")

    return ClientInfo.from_orm(client)


OverviewCache = client_utils.ClientCacheDependency(
    ClientCacheKeys.OVERVIEW, ClientOverviewCache
)


@router.get("/client/overview", response_model=ClientOverview)
async def get_client_overview(
    background_tasks: BackgroundTasks,
    cache: client_utils.ClientCache[ClientOverviewCache] = Depends(OverviewCache),
    query_params: ClientQueryParams = Depends(get_query_params),
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    any_client = False
    if not query_params.client_ids:
        # query_params.client_ids = [None]
        any_client = True

    raw_overviews, non_cached = await cache.read(db)

    if non_cached:
        # All clients that weren't found in cache have to be read from DB
        clients = await client_utils.get_user_clients(
            user, None if any_client else non_cached, db=db
        )
        for client in clients:
            daily = await db_all(
                client.daily_balance_stmt(
                    since=query_params.since,
                    to=query_params.to,
                ),
                session=db,
            )
            transfers = await db_all(
                select(TransferDB)
                .where(
                    TransferDB.client_id == client.id,
                    time_range(Execution.time, query_params.since, query_params.to),
                    opt_eq(TransferDB.coin, query_params.currency),
                )
                .join(TransferDB.execution),
                session=db,
            )

            start = await client.get_exact_balance_at_time(query_params.since)
            latest = await client.get_latest_balance(redis=redis)

            overview = ClientOverviewCache(
                id=client.id,
                total=FullInterval.create(
                    prev=start.get_currency(query_params.currency),
                    current=latest.get_currency(query_params.currency),
                    offset=sum(
                        transfer.size if query_params.currency else transfer.amount
                        for transfer in transfers
                    ),
                ),
                daily_balance=daily,
                transfers=transfers,
            )
            background_tasks.add_task(
                cache.write,
                client.id,
                overview,
            )
            raw_overviews.append(overview)

    ts1 = time.perf_counter()
    if raw_overviews:
        all_daily = []
        latest_by_client = {}

        by_day = groupby(
            sorted(
                itertools.chain.from_iterable(
                    raw.daily_balance for raw in raw_overviews
                ),
                key=lambda b: b.time,
            ),
            lambda b: date_string(b.time),
        )

        for _, balances in by_day.items():
            present_ids = [balance.client_id for balance in balances]
            present_excluded = [
                balance
                for client_id, balance in latest_by_client.items()
                if client_id not in present_ids
            ]

            current = sum_iter(balances)
            others = sum_iter(present_excluded) if present_excluded else None

            all_daily.append((current + others) if others else current)
            for balance in balances:
                latest_by_client[balance.client_id] = balance

        all_transfers = sorted(
            sum_iter(overview.transfers for overview in raw_overviews),
            key=lambda transfer: transfer.time,
        )

        intervals = create_intervals(all_daily, all_transfers, query_params.currency)

        query_params.limit = 5
        query_params.order = "desc"

        recent_trades = await client_utils.query_trades(
            TradeDB.initial, user_id=user.id, query_params=query_params, db=db
        )
        overview = ClientOverview.construct(
            intervals=intervals,
            total=sum_iter(raw.total for raw in raw_overviews),
            transfers=all_transfers,
            recent_trades=[BasicTrade.from_orm(trade) for trade in recent_trades],
        )

        encoded = jsonable_encoder(overview)
        ts2 = time.perf_counter()
        print("encode", ts2 - ts1)
        return CustomJSONResponse(content=encoded)
    else:
        raise BadRequest("Invalid client id", 40000)


class PnlStat(OrmBaseModel):
    win: Decimal
    loss: Decimal
    total: Decimal


class PnlStats(OrmBaseModel):
    date: Optional[date]
    gross: PnlStat
    net: Decimal
    commissions: Decimal
    count: int


# Cache query
performance_base_select = select(
    TradeDB.count,
    TradeDB.gross_win,
    TradeDB.gross_loss,
    TradeDB.total_commissions_stmt.label("total_commissions"),
)


@router.get("/client/performance", response_model=ResponseModel[list[PnlStats]])
async def get_client_performance(
    interval: IntervalType = Query(default=None),
    trade_id: list[int] = Query(default=None),
    query_params: ClientQueryParams = Depends(get_query_params),
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    ts1 = time.perf_counter()

    if interval:
        stmt = performance_base_select.add_columns(
            func.date_trunc("day", TradeDB.open_time).label("date")
        ).group_by(
            # func.date_trunc('day', TradeDB.open_time).label('date')
            TradeDB.open_time
        )
    else:
        stmt = performance_base_select

    stmt = add_client_checks(
        stmt.where(
            time_range(TradeDB.open_time, query_params.since, query_params.to),
            TradeDB.id.in_(trade_id) if trade_id else True,
        ).join(TradeDB.client),
        user_id=user.id,
        client_ids=query_params.client_ids,
    )
    results = (await db.execute(stmt)).all()
    ts2 = time.perf_counter()
    response = []

    for result in results:
        gross_win = result.gross_win or 0
        gross_loss = result.gross_loss or 0
        commissions = result.total_commissions or 0

        response.append(
            PnlStats.construct(
                gross=PnlStat.construct(
                    win=gross_win, loss=abs(gross_loss), total=gross_win + gross_loss
                ),
                commissions=commissions,
                net=gross_win + gross_loss - commissions,
                date=result.date if interval else None,
                count=result.count,
            )
        )

    ts3 = time.perf_counter()
    print("Performance")
    print(ts2 - ts1)
    print(ts3 - ts2)
    return OK(result=jsonable_encoder(response))


@router.get("/client/symbols", response_model=ResponseModel[list[str]])
async def get_client_symbols(
    query_params: ClientQueryParams = Depends(get_query_params),
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    stmt = add_client_checks(
        select(TradeDB.symbol).join(TradeDB.client).distinct(),
        user_id=user.id,
        client_ids=query_params.client_ids,
    )

    results = await db_all(stmt, session=db)

    return OK(result=jsonable_encoder(results))


async def get_balance(
    at: Optional[datetime],
    client_ids: Optional[set[InputID]],
    user_id: UUID,
    db: AsyncSession,
    currency: Optional[str] = None,
    latest=True,
) -> Balance:
    stmt = (
        select(BalanceDB)
        .distinct(BalanceDB.client_id)
        .where((BalanceDB.time < at if latest else BalanceDB.time > at) if at else True)
        .join(BalanceDB.client)
        .order_by(
            BalanceDB.client_id, desc(BalanceDB.time) if latest else asc(BalanceDB.time)
        )
    )

    if currency:
        stmt = stmt.join(
            Amount, and_(Amount.balance_id == BalanceDB.id, Amount.currency == currency)
        )

    balances: list[BalanceDB] = await db_all(
        add_client_checks(stmt, user_id=user_id, client_ids=client_ids),
        BalanceDB.client,
        session=db,
    )
    if balances:
        return Balance.sum([balance.get_currency(currency) for balance in balances])
    else:
        raise NotFound("No matching balance")


auth = get_auth_grant_dependency(ChapterGrant)


@router.get("/client/balance", response_model=Balance)
async def get_client_balance(
    to: Optional[datetime] = Query(None),
    since: Optional[datetime] = Query(default=None),
    client_ids: Optional[set[InputID]] = Query(default=None, alias="client_id"),
    chapter_id: Optional[InputID] = Query(None),
    currency: Optional[str] = Query(None),
    grant: AuthGrant = Depends(auth),
    db: AsyncSession = Depends(get_db),
):
    params = ClientQueryParams.construct(since=since, to=to, client_ids=client_ids)

    if not grant.root:
        if chapter_id:
            node = await db_first(
                Chapter.query_nodes(
                    chapter_id, node_type="balanceDisplay", query_params=params
                ),
                session=db,
            )
            if not node:
                raise Unauthorized()

    balance = None

    if not to or to > utc_now():
        if not client_ids:
            client_ids = await grant.user.get_client_ids()

        missing = set()
        for client_id in client_ids:
            current = await ClientRedis(
                user_id=grant.user_id, client_id=client_id
            ).get_balance()
            if current:
                current = current.get_currency(currency)
                balance = balance + current if balance else current
            else:
                missing.add(client_id)
    else:
        missing = client_ids

    if missing or missing is None:
        other = await get_balance(
            to, missing, grant.user_id, db, currency=currency, latest=True
        )
        balance = balance + other if balance else other

    if since or True:
        gain_balance = await get_balance(
            since, client_ids, grant.user_id, db, currency=currency, latest=False
        )

        offset = await Client.get_total_transfered(
            client_ids, grant.user_id, since=since, to=to, ccy=currency, db=db
        )
        balance.set_gain(gain_balance, offset)

    return balance


@router.get("/client/balance/history")
async def get_client_history(
    balance_id: Optional[set[int]] = Query(None),
    query_params: ClientQueryParams = Depends(get_query_params),
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    balances = await BalanceDB.query(
        time_col=BalanceDB.time,
        user_id=user.id,
        ids=balance_id,
        params=query_params,
        db=db,
    )
    return [Balance.from_orm(balance) for balance in balances]


@router.get("/client/{client_id}", response_model=ClientDetailed)
async def get_client(
    client_id: InputID,
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    client = await client_utils.get_user_client(
        user, client_id, Client.trade_template, Client.events, db=db
    )

    if client:
        return ClientDetailed.from_orm(client)
    else:
        raise NotFound("Invalid id")


@router.delete("/client/{client_id}")
async def delete_client(
    client_id: InputID,
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        add_client_checks(delete(Client), user.id, {client_id}),
    )
    await db.commit()
    if result.rowcount == 1:
        return OK(detail="Success")
    else:
        raise NotFound(detail="Invalid ID")


@router.patch("/client/{client_id}", response_model=ClientDetailed)
async def update_client(
    client_id: InputID,
    body: ClientEdit,
    user: User = Depends(CurrentUser),
    http_session: aiohttp.ClientSession = Depends(get_http_session),
    db: AsyncSession = Depends(get_db),
):
    client = await client_utils.get_user_client(
        user, client_id, Client.trade_template, Client.events, db=db
    )

    if client:
        for k, v in body.dict(exclude_unset=True, exclude={"api"}).items():
            setattr(client, k, v)

        if body.api:
            for k, v in body.api.dict().items():
                setattr(client, k, v)

            client.state = ClientState.OK

            exchange_cls = EXCHANGES[client.exchange]
            exchange = exchange_cls(
                client, http_session, db_maker=async_maker, commit=False
            )
            try:
                await exchange.get_balance()
            except InvalidClientError:
                raise BadRequest("Invalid API credentials")
            except ResponseError:
                raise InternalError()

        client.validate()
    else:
        raise BadRequest("Invalid client id")

    await db.commit()
    return ClientDetailed.from_orm(client)

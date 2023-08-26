# ruff: noqa: F401

from operator import and_

from sqlalchemy import desc, select, func
from sqlalchemy.orm import relationship, aliased

import lib.db.models.alert
import lib.db.models.balance
import lib.db.models.client
import lib.db.models.coin
import lib.db.models.event
import lib.db.models.execution
import lib.db.models.label
import lib.db.models.pnldata
import lib.db.models.mixins.serializer
import lib.db.models.trade
import lib.db.models.transfer
import lib.db.models.user
import lib.db.models.evententry
import lib.db.models.action

import lib.db.models.editing.chapter as chapter
import lib.db.models.editing.template as template
import lib.db.models.editing.journal as journal
import lib.db.models.editing.preset as preset

import lib.db.models.discord.discorduser
import lib.db.models.discord.guild as guild
import lib.db.models.discord.guildassociation as ga
import lib.db.models.authgrant

Client = client.Client
User = user.User
BalanceDB = balance.Balance
Amount = balance.Amount
Balance = balance.Balance
Chapter = chapter.Chapter
Execution = execution.Execution
TradeDB = trade.Trade
Trade = trade.Trade
Transfer = transfer.Transfer
TransferDB = transfer.Transfer
EventEntry = evententry.EventEntry
EventScore = evententry.EventScore
Event = event.Event
GuildAssociation = ga.GuildAssociation

partioned_balance = select(
    BalanceDB,
    func.row_number()
    .over(order_by=desc(BalanceDB.time), partition_by=BalanceDB.client_id)
    .label("index"),
).alias()

partioned_history = aliased(BalanceDB, partioned_balance)

client.Client.recent_history = relationship(
    partioned_history,
    lazy="noload",
    primaryjoin=and_(
        client.Client.id == partioned_history.client_id, partioned_balance.c.index <= 3
    ),
)

# ChildChapter = aliased(Chapter)
# child_ids = select(ChildChapter.id)
#
# query = aliased(Chapter, child_ids)
#
# Chapter.child_ids = relationship(
#    query,
#    lazy='noload',
#    primaryjoin=ChildChapter.parent_id == Chapter.id,
#    viewonly=True,
#    uselist=True
# )


current = select(EventScore).order_by(desc(EventScore.time)).limit(1).alias()

latest = aliased(EventScore, current)


# EventScore.current_rank = relationship(EventRank,
#                                       lazy='joined',
#                                       uselist=False
#                                       )
# EventScore.current_rank = relationship(latest, lazy='noload', uselist=False)


__all__ = [
    "balance",
    "Balance",
    "BalanceDB",
    "Transfer",
    "TransferDB",
    "client",
    "trade",
    "user",
    "Client",
    "Execution",
    "TradeDB",
    "GuildAssociation",
]

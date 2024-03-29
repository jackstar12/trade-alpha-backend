from __future__ import annotations

from datetime import date
from typing import Optional, TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import orm, select, func, literal, or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import aliased

from lib.db.dbasync import opt_eq
from lib.db.models.editing.pagemixin import PageMixin, cmp_dates
from lib.db.models.mixins.serializer import Serializer
from lib.db.dbsync import Base, BaseMixin
from lib.models import BaseModel
from lib.models.document import DocumentModel, TradeData

if TYPE_CHECKING:
    from lib.db.models.client import ClientQueryParams

balance_association = sa.Table(
    "balance_association",
    Base.metadata,
    sa.Column(
        "balance_id", sa.ForeignKey("balance.id", ondelete="CASCADE"), primary_key=True
    ),
    sa.Column(
        "chapter_id", sa.ForeignKey("chapter.id", ondelete="CASCADE"), primary_key=True
    ),
)

chapter_trade_association = sa.Table(
    "chapter_trade_association",
    Base.metadata,
    sa.Column(
        "trade_id", sa.ForeignKey("trade.id", ondelete="CASCADE"), primary_key=True
    ),
    sa.Column(
        "chapter_id", sa.ForeignKey("chapter.id", ondelete="CASCADE"), primary_key=True
    ),
)


class ChapterData(BaseModel):
    start_date: date
    end_date: date

    # def get_end_date(self, interval: timedelta):
    #     return self.start_date + interval


class Chapter(Base, Serializer, BaseMixin, PageMixin):
    __tablename__ = "chapter"

    # Identifiers
    journal_id = sa.Column(
        sa.ForeignKey("journal.id", ondelete="CASCADE"), nullable=False
    )
    template_id = sa.Column(
        sa.ForeignKey("template.id", ondelete="SET NULL"), nullable=True
    )
    data: Optional[ChapterData] = sa.Column(
        ChapterData.get_sa_type(validate=True), nullable=True
    )

    journal = orm.relationship("Journal", lazy="noload")
    template = orm.relationship("Template", lazy="noload")

    @hybrid_property
    def start_date(self):
        return self.data.start_date

    @hybrid_property
    def end_date(self):
        return self.data.end_date

    @hybrid_property
    def all_data(self):
        results = []

        def recursive(current: DocumentModel):
            if current.attrs and "data" in current.attrs:
                results.append(TradeData(**current.attrs["data"]))

            if current.content:
                for node in current.content:
                    recursive(node)

        recursive(self.doc)

        return results

    @classmethod
    def query_nodes(
        cls,
        root_id: Optional[int] = None,
        node_type: Optional[str] = None,
        query_params: Optional[ClientQueryParams] = None,
        journal_id: Optional[int] = None,
        trade_ids: Optional[list[int]] = None,
    ):
        included = (
            select(cls.id, cls.doc)
            .filter(
                or_(
                    opt_eq(cls.id, root_id),
                    opt_eq(cls.parent_id, root_id),
                ),
                opt_eq(cls.journal_id, journal_id),
            )
            .cte(name="included", recursive=True)
        )

        included_alias = aliased(included, name="parent")
        chapter_alias = aliased(cls, name="child")

        included = included.union_all(
            select(chapter_alias.id, chapter_alias.doc).filter(
                chapter_alias.parent_id == included_alias.c.id
            )
        )

        tree = select(
            func.jsonb_array_elements(included.c.doc["content"])
            .cast(JSONB)
            .label("node")
        ).cte(name="nodes", recursive=True)

        attrs = tree.c.node["attrs"]

        tree = tree.union(
            select(func.jsonb_array_elements(tree.c.node["content"]).cast(JSONB)).where(
                func.jsonb_exists(attrs, "data"),
                opt_eq(tree.c.node["type"].astext, node_type),
            )
        )
        data = tree.c.node["attrs"]["data"]

        if query_params:
            whereas = (
                cmp_dates(data["dates"]["to"], query_params.to),
                cmp_dates(data["dates"]["since"], query_params.since),
                or_(
                    data["clientIds"] == JSONB.NULL,
                    # data['clientIds'].contains(list(map(str, query_params.client_ids)))
                    data["clientIds"].contains(
                        [str(id) for id in query_params.client_ids]
                    ),
                ),
                # or_(
                #    data['tradeIds'] == JSONB.NULL,
                #    data['tradeIds'].contains(trade_ids and map_list(str, trade_ids))
                # )
            )
        else:
            whereas = tuple()

        return select(data).where(data != JSONB.NULL, *whereas)

    @all_data.expression
    def all_data(cls):
        """
        WITH RECURSIVE _tree (key, value) AS (
          SELECT
            NULL   AS key,
            chapter.doc AS value FROM chapter WHERE chapter.id=272
          UNION ALL
          (WITH typed_values AS (SELECT jsonb_typeof(value) as typeof, value FROM _tree)
           SELECT v.*
             FROM typed_values, LATERAL jsonb_each(value) v
             WHERE typeof = 'object' and jsonb_exists(typed_values.value, 'content')
           UNION ALL
           SELECT NULL, element
             FROM typed_values, LATERAL jsonb_array_elements(value) element
             WHERE typeof = 'array'
          )
        )
        SELECT key, value
          FROM _tree


        """

        # https://stackoverflow.com/questions/30132568/collect-recursive-json-keys-in-postgres
        # http://tatiyants.com/how-to-navigate-json-trees-in-postgres-using-recursive-ctes/

        cte = select(literal("NULL").label("key"), Chapter.doc.label("doc")).cte(
            recursive=True
        )
        cte_alias = cte.alias()

        typed_values = select(
            func.jsonb_typeof(cte_alias.c.doc).label("typeof"),
            cte_alias.c.doc.label("value"),
        ).cte(name="typed_values")

        array_elemenets = (
            select(
                literal("NULL").label("key"),
                func.jsonb_array_elements(typed_values.c.value).label("element"),
            )
            .subquery()
            .lateral()
        )

        result = typed_values.union_all(
            select(array_elemenets.c.key, array_elemenets.c.element)
            .select_from()
            .where(typed_values.c.typeof == "array")
        )

        return select(result).scalar_subquery()

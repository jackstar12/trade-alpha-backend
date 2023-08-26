from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.users import CurrentUser
from api.utils.responses import OK, NotFound
from lib.db.dbasync import db_unique, db_all
from lib.db.models.editing import Journal, Chapter
from lib.db.models.editing.pagemixin import PageMixin
from lib.db.models.editing.template import Template as DbTemplate
from lib.db.models.user import User
from lib.models import InputID
from lib.models.page import PageInfo, FullPage

router = APIRouter(tags=["page"], dependencies=[], prefix="/pages")


async def query_templates(
    template_ids: list[int],
    *where,
    user_id: UUID,
    session: AsyncSession,
    raise_not_found=True,
    eager=None,
    **filters
) -> DbTemplate | list[DbTemplate]:
    func = db_unique if len(template_ids) == 1 else db_all
    template = await func(
        select(DbTemplate)
        .where(
            DbTemplate.id.in_(template_ids) if template_ids else True,
            DbTemplate.user_id == user_id,
            *where,
        )
        .filter_by(**filters),
        *(eager or []),
        session=session,
    )
    if not template and raise_not_found:
        raise HTTPException(404, "Chapter not found")
    return template


@router.get("/search", response_model=list[PageInfo])
async def create_template(
    page_type: Literal["template", "chapter"],
    title: Optional[str] = None,
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    table: type[PageMixin]

    if page_type == "template":
        table = DbTemplate
        base = select(DbTemplate).where(DbTemplate.user_id == user.id)
    else:
        table = Chapter
        base = select(Chapter).where(Journal.user_id == user.id).join(Chapter.journal)

    results = await db_all(
        base.where(
            func.lower(table.title).like("%" + (title or "").lower() + "%")
        ).order_by(desc(table.created_at)),
        Chapter.journal,
        session=db,
    )

    return OK(result=[PageInfo.from_orm(result) for result in results])


@router.get("/{page_id}", response_model=FullPage)
async def get_page(
    page_type: Literal["template", "chapter"],
    page_id: InputID,
    heading_id: Optional[str] = None,
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    table: type[PageMixin]

    if page_type == "template":
        table = DbTemplate
        base = select(DbTemplate).where(DbTemplate.user_id == user.id)
    else:
        table = Chapter
        base = select(Chapter).where(Journal.user_id == user.id).join(Chapter.journal)

    result: Chapter | DbTemplate = await db_unique(
        base.where(table.id == page_id), Chapter.journal, session=db
    )

    if result:
        return OK(
            result=FullPage(
                id=result.id,
                title=result.title,
                data=result.data,
                created_at=result.created_at,
                journal=result.journal,
                doc=result.doc.get_from_heading(heading_id)
                if heading_id
                else result.doc,
            )
        )
    else:
        return NotFound()

import itertools
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.routers.journal import query_journal
from api.routers.template import query_templates
from api.dependencies import get_db
from api.users import CurrentUser, get_auth_grant_dependency
from api.models.completejournal import DetailedChapter, ChapterCreate, ChapterUpdate
from api.utils.responses import OK
from lib.db.dbasync import db_unique
from lib.db.models.authgrant import AuthGrant, ChapterGrant, AssociationType
from lib.db.models.editing.chapter import Chapter as DbChapter
from lib.db.models.editing.journal import Journal
from lib.db.models.user import User
from lib.models import InputID

router = APIRouter(tags=["chapter"], prefix="/chapter")


async def query_chapter(
    chapter_id: int, user_id: UUID, *eager, session: AsyncSession, **filters
) -> Optional[DbChapter]:
    chapter = await db_unique(
        select(DbChapter)
        .filter(DbChapter.id == chapter_id, Journal.user_id == user_id)
        .filter_by(**filters)
        .join(DbChapter.journal),
        session=session,
        *eager
    )
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    return chapter


@router.get("/{chapter_id}", response_model=DetailedChapter)
async def get_chapter(
    chapter_id: InputID,
    grant: AuthGrant = Depends(get_auth_grant_dependency(ChapterGrant)),
    db: AsyncSession = Depends(get_db),
):
    chapter = await query_chapter(
        chapter_id,
        grant.user_id,
        grant.is_root_for(AssociationType.CHAPTER) and DbChapter.grants,
        DbChapter.template,
        # DbChapter.trades,
        session=db,
    )

    return OK(result=DetailedChapter.from_orm(chapter).dict(exclude_none=True))


@router.get("/{chapter_id}/data", response_model=DetailedChapter)
async def get_chapter_data(
    chapter_id: InputID,
    grant: AuthGrant = Depends(get_auth_grant_dependency(ChapterGrant)),
    db: AsyncSession = Depends(get_db),
):
    chapter = await query_chapter(
        chapter_id,
        grant.user_id,
        # DbChapter.trades,
        # DbChapter.balances,
        session=db,
    )

    result = await db.scalars(DbChapter.query_nodes())

    childs = result.all()

    set(
        itertools.chain.from_iterable(
            child["tradeIds"] for child in childs if "tradeIds" in child
        )
    )

    return DetailedChapter.from_orm(chapter)


@router.patch("/{chapter_id}")
async def update_chapter(
    chapter_id: InputID,
    body: ChapterUpdate,
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    chapter = await query_chapter(chapter_id, user.id, session=db)
    if "doc" in body.__fields_set__:
        chapter.doc = body.doc
    if "data" in body.__fields_set__:
        chapter.data = body.data
    if "parent_id" in body.__fields_set__:
        chapter.parent_id = body.parent_id
    await db.commit()
    return OK("OK")


@router.post("", response_model=DetailedChapter)
async def create_chapter(
    body: ChapterCreate,
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    journal = await query_journal(body.journal_id, user.id, Journal.clients, session=db)

    template = None
    if body.template_id:
        template = await query_templates(
            [body.template_id], user_id=user.id, session=db
        )

    new_chapter = journal.create_chapter(body.parent_id, template)

    db.add(new_chapter)
    await db.commit()
    return DetailedChapter.from_orm(new_chapter)


@router.delete("/{chapter_id}")
async def delete_chapter(
    chapter_id: InputID, user: User = Depends(CurrentUser), db=Depends(get_db)
):
    chapter = await query_chapter(chapter_id, user.id, session=db)
    await db.delete(chapter)
    await db.commit()
    return OK("OK")

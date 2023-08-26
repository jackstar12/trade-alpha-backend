import operator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.models.template import (
    TemplateUpdate,
    TemplateInfo,
    TemplateCreate,
    TemplateDetailed,
)
from api.users import CurrentUser, get_auth_grant_dependency, DefaultGrant
from api.utils.responses import OK, CustomJSONResponse, NotFound
from lib.db.dbasync import db_unique, db_all, db_del_filter, opt_op, wrap_greenlet
from lib.db.models import Client
from lib.db.models.authgrant import TemplateGrant, AuthGrant, AssociationType
from lib.db.models.editing import Journal
from lib.db.models.editing.template import Template as DbTemplate, TemplateType
from lib.db.models.user import User
from lib.models import InputID

router = APIRouter(
    tags=["template"],
    dependencies=[],
    responses={
        401: {"detail": "Wrong Email or Password"},
        400: {"detail": "Email is already used"},
    },
)


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


@router.post("/template", response_model=TemplateInfo)
async def create_template(
    body: TemplateCreate,
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    template = DbTemplate(user_id=user.id, type=body.type)

    db.add(template)
    await db.flush()

    if body.journal_id:
        await db.execute(
            update(Journal)
            .where(Journal.id == body.journal_id, Journal.user_id == user.id)
            .values(default_template_id=template.id)
        )

    if body.client_id:
        await db.execute(
            update(Client)
            .where(Client.id == body.client_id, Client.user_id == user.id)
            .values(trade_template_id=template.id)
        )

    await db.commit()
    return TemplateInfo.from_orm(template)


auth = get_auth_grant_dependency(TemplateGrant)


@router.get("/template/{template_id}", response_model=TemplateDetailed)
async def get_template(
    template_id: InputID,
    template_type: TemplateType = Query(default=None),
    grant: AuthGrant = Depends(auth),
    db: AsyncSession = Depends(get_db),
):
    template = await query_templates(
        [template_id],
        opt_op(DbTemplate.type, template_type, operator.eq),
        eager=[
            DbTemplate.grants if grant.is_root_for(AssociationType.TEMPLATE) else None
        ],
        user_id=grant.user_id,
        session=db,
    )
    return TemplateDetailed.from_orm(template)


@router.get("/template", response_model=list[TemplateInfo])
@wrap_greenlet
def get_templates(
    template_type: TemplateType = Query(default=None),
    grant: AuthGrant = Depends(DefaultGrant),
):
    return CustomJSONResponse(
        content=jsonable_encoder(
            TemplateInfo.from_orm(template)
            for template in (
                grant.user.templates
                if grant.is_root_for(AssociationType.TEMPLATE)
                else grant.templates
            )
        )
    )


@router.patch("/template/{template_id}")
async def update_template(
    template_id: InputID,
    body: TemplateUpdate,
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        update(DbTemplate)
        .where(DbTemplate.id == template_id, DbTemplate.user_id == user.id)
        .values(**body.dict(exclude_none=True))
    )
    await db.commit()

    if result.rowcount == 0:
        raise NotFound("Invalid template id")

    return OK("Updated")


@router.delete("/template/{template_id}")
async def delete_template(
    template_id: InputID,
    user: User = Depends(CurrentUser),
    db: AsyncSession = Depends(get_db),
):
    result = await db_del_filter(
        DbTemplate, id=template_id, user_id=user.id, session=db
    )
    await db.commit()

    if result.rowcount == 0:
        raise NotFound("Invalid template id")

    return OK("Deleted")

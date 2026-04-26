from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..db.models import MemoryRow
from ..db.repository import Repository
from ..db.session import get_session
from ..gateways.embeddings_gateway import EmbeddingsGateway
from ..services.knowledge_service import KnowledgeService

router = APIRouter()


def verify_api_key(authorization: Annotated[str | None, Header()] = None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    if authorization[7:] != settings.backend_api_key:
        raise HTTPException(401, "invalid api key")


@router.post("/admin/sync-knowledge", dependencies=[Depends(verify_api_key)])
async def sync_knowledge(session: AsyncSession = Depends(get_session)):  # noqa: B008
    repo = Repository(session)
    user = await repo.get_or_create_user(settings.default_user_handle)
    svc = KnowledgeService(EmbeddingsGateway())
    stats = await svc.sync_directory(session, user.id)
    return {"ok": True, "stats": stats}


@router.post("/admin/memory/{memory_id}/expire", dependencies=[Depends(verify_api_key)])
async def expire_memory(
    memory_id: str, session: AsyncSession = Depends(get_session)  # noqa: B008
):
    repo = Repository(session)
    m = await repo.get_memory(memory_id)
    if not m:
        raise HTTPException(404, "memory not found")
    await repo.expire_memory(memory_id)
    await session.commit()
    return {"ok": True, "id": memory_id, "content": m.content}


@router.get("/admin/memory-list", dependencies=[Depends(verify_api_key)])
async def memory_list(
    include_expired: bool = False,
    session: AsyncSession = Depends(get_session),  # noqa: B008
):
    repo = Repository(session)
    user = await repo.get_or_create_user(settings.default_user_handle)
    q = select(MemoryRow).where(MemoryRow.user_id == user.id)
    if not include_expired:
        q = q.where(MemoryRow.valid_until.is_(None))
    q = q.order_by(MemoryRow.created_at.desc()).limit(200)
    rows = (await session.execute(q)).scalars().all()
    return [
        {
            "id": m.id,
            "kind": m.kind,
            "content": m.content,
            "valid_from": m.valid_from.isoformat() if m.valid_from else None,
            "valid_until": m.valid_until.isoformat() if m.valid_until else None,
            "superseded_by": m.superseded_by,
            "confidence": m.confidence,
            "created_at": m.created_at.isoformat(),
        }
        for m in rows
    ]

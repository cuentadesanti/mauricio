from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
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
async def sync_knowledge(session: AsyncSession = Depends(get_session)):
    repo = Repository(session)
    user = await repo.get_or_create_user(settings.default_user_handle)
    svc = KnowledgeService(EmbeddingsGateway())
    stats = await svc.sync_directory(session, user.id)
    return {"ok": True, "stats": stats}


@router.get("/admin/memory-list", dependencies=[Depends(verify_api_key)])
async def memory_list(session: AsyncSession = Depends(get_session)):
    """Útil para inspeccionar qué se ha extraído."""
    from sqlalchemy import select

    from ..db.models import MemoryRow

    repo = Repository(session)
    user = await repo.get_or_create_user(settings.default_user_handle)
    res = await session.execute(
        select(MemoryRow)
        .where(MemoryRow.user_id == user.id)
        .order_by(MemoryRow.created_at.desc())
        .limit(100)
    )
    return [
        {"id": m.id, "kind": m.kind, "content": m.content, "created_at": m.created_at.isoformat()}
        for m in res.scalars().all()
    ]

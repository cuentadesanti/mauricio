from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import admin, chat, health
from .core.config import settings
from .db.repository import Repository
from .db.session import SessionLocal
from .gateways.embeddings_gateway import EmbeddingsGateway
from .services.knowledge_service import KnowledgeService


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with SessionLocal() as session:
            repo = Repository(session)
            user = await repo.get_or_create_user(settings.default_user_handle)
            svc = KnowledgeService(EmbeddingsGateway())
            stats = await svc.sync_directory(session, user.id)
            print(f"[boot] knowledge sync: {stats}")
    except Exception as e:
        print(f"[boot] knowledge sync failed (non-fatal): {e}")
    yield


app = FastAPI(title="Personal AI Backend", version="0.0.2", lifespan=lifespan)

app.include_router(health.router)
app.include_router(chat.router, prefix="/v1")
app.include_router(admin.router)

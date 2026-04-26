from fastapi import FastAPI

from .api import chat, health

app = FastAPI(title="Personal AI Backend", version="0.0.1")

app.include_router(health.router)
app.include_router(chat.router, prefix="/v1")

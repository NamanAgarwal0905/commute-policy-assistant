"""Composition root: creates the app and wires routers. No business logic here."""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.api import auth, policies, questions
from app.core.container import Container, get_container

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    container = get_container()  # fails fast if JWT_SECRET is missing or too short
    if container.settings.auto_seed:
        from app.services.seed import seed

        seed(container)
    # Warm the embedding model so the first real question is not slow.
    container.store.query("warmup", "none", "warm up", 1)
    yield


app = FastAPI(title="Commute Policy Assistant", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(questions.router)
app.include_router(policies.router)


@app.get("/health")
def health(container: Container = Depends(get_container)):
    container.db.company_exists("acme")  # raises if SQLite is unreachable
    container.store.count("acme", "none")  # raises if Chroma is unreachable
    return {"status": "ok"}

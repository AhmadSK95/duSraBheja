"""FastAPI application for the private brain service."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.routes import router


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(title="duSraBheja API", lifespan=lifespan)
app.include_router(router)

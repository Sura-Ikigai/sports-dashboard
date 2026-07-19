from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import os

from database import SessionLocal
from services import nba_service
from routers import nba

scheduler = AsyncIOScheduler()


async def scheduled_sync():
    db = SessionLocal()
    try:
        await nba_service.sync_teams(db)
        await nba_service.sync_games(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(scheduled_sync, "interval", minutes=15)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(nba.router)

@app.get("/health")
def health_check():
    return {"status": "ok", "environment": os.getenv("ENVIRONMENT", "local")}

@app.get("/")
def root():
    return {"message": "Sports Dashboard API"}

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

from routers import nba

app = FastAPI()

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

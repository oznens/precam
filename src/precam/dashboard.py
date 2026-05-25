from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from .db import Kol, SessionLocal, Signal, init_db

TEMPLATES_DIR = Path(__file__).parent / "templates"
app = FastAPI(title="precam dashboard")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.on_event("startup")
async def _startup() -> None:
    await init_db()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, early: int = 0, limit: int = 100):
    async with SessionLocal() as s:
        q = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
        if early:
            q = (
                select(Signal)
                .where(Signal.is_early == True)  # noqa: E712
                .order_by(Signal.created_at.desc())
                .limit(limit)
            )
        res = await s.execute(q)
        signals = res.scalars().all()

        total = (await s.execute(select(func.count(Signal.id)))).scalar_one()
        early_count = (
            await s.execute(
                select(func.count(Signal.id)).where(Signal.is_early == True)  # noqa: E712
            )
        ).scalar_one()
        kols = (await s.execute(select(Kol).order_by(Kol.weight.desc()))).scalars().all()

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "signals": signals,
            "kols": kols,
            "total": total,
            "early_count": early_count,
            "early_only": bool(early),
        },
    )


@app.get("/api/signals")
async def api_signals(limit: int = 100, early: bool = False):
    async with SessionLocal() as s:
        q = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
        if early:
            q = (
                select(Signal)
                .where(Signal.is_early == True)  # noqa: E712
                .order_by(Signal.created_at.desc())
                .limit(limit)
            )
        res = await s.execute(q)
        return [r.model_dump() for r in res.scalars().all()]

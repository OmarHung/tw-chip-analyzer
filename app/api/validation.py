"""前瞻驗證 API:分數 vs 之後實現報酬(§28 成功標準的活體追蹤)。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.forward_report import build_forward_report

router = APIRouter(prefix="/api/validation", tags=["validation"])


@router.get("/forward")
async def forward(session: AsyncSession = Depends(get_session)) -> dict:
    return await build_forward_report(session)

"""個股籌碼分析 API（里程碑 E 充實）。"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/stocks", tags=["stocks"])

"""全市場掃描 API（里程碑 E 充實）。"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["scanner"])

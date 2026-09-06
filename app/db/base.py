"""SQLAlchemy 2.0 declarative base。

所有 ORM model 繼承 Base；models 於 app/db/models/ 定義。
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass

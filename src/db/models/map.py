"""
Map ORM model — stores 2D occupancy grid floorplans, waypoints, and SLAM metadata.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, utcnow


class MapModel(Base):
    """A 2D occupancy grid map representing a scanned house floor plan."""

    __tablename__ = "maps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resolution_cm: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=400)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=400)
    origin_x_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    origin_y_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    
    # Zlib-compressed 2D occupancy grid byte array
    grid_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    
    # JSON array of labeled waypoints: [{"name": "kitchen", "x": 2.5, "y": 1.2}]
    waypoints_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="[]")
    
    # Active loaded map flag
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:
        return f"<MapModel id={self.id} name={self.name!r} active={self.is_active}>"

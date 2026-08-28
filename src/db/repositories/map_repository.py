"""
Map repository — CRUD operations for 2D occupancy grid floor maps.
"""

from typing import List, Optional

from sqlalchemy import desc, select, update
from sqlalchemy.orm import Session

from src.db.base import SessionLocal
from src.db.models.map import MapModel


def create_map(
    name: str,
    grid_data: bytes,
    width: int = 400,
    height: int = 400,
    resolution_cm: float = 5.0,
    origin_x_m: float = 0.0,
    origin_y_m: float = 0.0,
    waypoints_json: str = "[]",
    is_active: bool = False,
    session: Optional[Session] = None,
) -> MapModel:
    """Create and persist a new 2D occupancy grid map."""
    def _execute(s: Session) -> MapModel:
        if is_active:
            # Deactivate any currently active map
            s.execute(update(MapModel).values(is_active=False))

        map_obj = MapModel(
            name=name,
            grid_data=grid_data,
            width=width,
            height=height,
            resolution_cm=resolution_cm,
            origin_x_m=origin_x_m,
            origin_y_m=origin_y_m,
            waypoints_json=waypoints_json,
            is_active=is_active,
        )
        s.add(map_obj)
        s.commit()
        s.refresh(map_obj)
        return map_obj

    if session is not None:
        return _execute(session)
    with SessionLocal() as s:
        return _execute(s)


def get_map(map_id: int, session: Optional[Session] = None) -> Optional[MapModel]:
    """Retrieve a map by its primary key ID."""
    def _execute(s: Session) -> Optional[MapModel]:
        return s.get(MapModel, map_id)

    if session is not None:
        return _execute(session)
    with SessionLocal() as s:
        return _execute(s)


def get_active_map(session: Optional[Session] = None) -> Optional[MapModel]:
    """Retrieve the currently active map, if any."""
    def _execute(s: Session) -> Optional[MapModel]:
        stmt = select(MapModel).where(MapModel.is_active == True).limit(1)
        return s.scalars(stmt).first()

    if session is not None:
        return _execute(session)
    with SessionLocal() as s:
        return _execute(s)


def list_maps(limit: int = 100, session: Optional[Session] = None) -> List[MapModel]:
    """List stored maps ordered by updated_at descending."""
    def _execute(s: Session) -> List[MapModel]:
        stmt = select(MapModel).order_by(desc(MapModel.updated_at)).limit(limit)
        return list(s.scalars(stmt).all())

    if session is not None:
        return _execute(session)
    with SessionLocal() as s:
        return _execute(s)


def update_map(
    map_id: int,
    name: Optional[str] = None,
    grid_data: Optional[bytes] = None,
    waypoints_json: Optional[str] = None,
    is_active: Optional[bool] = None,
    session: Optional[Session] = None,
) -> Optional[MapModel]:
    """Update map fields."""
    def _execute(s: Session) -> Optional[MapModel]:
        map_obj = s.get(MapModel, map_id)
        if not map_obj:
            return None

        if name is not None:
            map_obj.name = name
        if grid_data is not None:
            map_obj.grid_data = grid_data
        if waypoints_json is not None:
            map_obj.waypoints_json = waypoints_json
        if is_active is not None:
            if is_active:
                s.execute(update(MapModel).where(MapModel.id != map_id).values(is_active=False))
            map_obj.is_active = is_active

        s.commit()
        s.refresh(map_obj)
        return map_obj

    if session is not None:
        return _execute(session)
    with SessionLocal() as s:
        return _execute(s)


def set_active_map(map_id: int, session: Optional[Session] = None) -> Optional[MapModel]:
    """Mark a map as active and deactivate all others."""
    return update_map(map_id=map_id, is_active=True, session=session)


def delete_map(map_id: int, session: Optional[Session] = None) -> bool:
    """Delete a map by its ID."""
    def _execute(s: Session) -> bool:
        map_obj = s.get(MapModel, map_id)
        if not map_obj:
            return False
        s.delete(map_obj)
        s.commit()
        return True

    if session is not None:
        return _execute(session)
    with SessionLocal() as s:
        return _execute(s)

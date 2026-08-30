"""
House floorplan map persistence and management endpoints (SQLite).
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.db.repositories.map_repository import delete_map, list_maps
from src.services.mapping_service import get_mapping_service
from src.web.auth import verify_credentials

router = APIRouter(prefix="/api/maps", tags=["maps"])


class SaveMapRequest(BaseModel):
    name: str = "House Floorplan"


@router.get("")
async def list_house_maps(_: str = Depends(verify_credentials)):
    """List all saved maps stored in SQLite."""
    maps = list_maps(limit=100)
    return [
        {
            "id": m.id,
            "name": m.name,
            "width": m.width,
            "height": m.height,
            "resolution_cm": m.resolution_cm,
            "is_active": m.is_active,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        }
        for m in maps
    ]


@router.post("")
async def save_current_map(req: SaveMapRequest, _: str = Depends(verify_credentials)):
    """Save the active occupancy grid map to SQLite."""
    mapping_svc = get_mapping_service()
    map_obj = mapping_svc.save_current_map(name=req.name)
    return {
        "status": "saved",
        "id": map_obj.id,
        "name": map_obj.name,
    }


@router.post("/{map_id}/load")
async def load_saved_map(map_id: int, _: str = Depends(verify_credentials)):
    """Load a previously saved map into the SLAM engine."""
    mapping_svc = get_mapping_service()
    success = mapping_svc.load_map(map_id)
    if not success:
        raise HTTPException(status_code=404, detail="Map not found")
    return {"status": "loaded", "map_id": map_id, "name": mapping_svc.map_name}


@router.delete("/{map_id}")
async def delete_saved_map(map_id: int, _: str = Depends(verify_credentials)):
    """Delete a saved map from SQLite."""
    success = delete_map(map_id)
    if not success:
        raise HTTPException(status_code=404, detail="Map not found")
    return {"status": "deleted", "map_id": map_id}

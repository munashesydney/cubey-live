"""Native Nav2 / SLAM Toolbox saved-map endpoints."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from src.services.navigation.cubey_nav_service import get_nav_service
from src.services.navigation.map_library import get_native_map_library
from src.web.auth import verify_credentials

router = APIRouter(prefix="/api/maps", tags=["maps"])


@router.get("")
async def list_house_maps(_: str = Depends(verify_credentials)):
    """List maps actually written by the live Nav2/SLAM mission."""
    return [native_map.to_dict() for native_map in get_native_map_library().list()]


@router.post("")
async def save_current_map(_: str = Depends(verify_credentials)):
    """Prevent the UI from silently saving the obsolete display-grid cache."""
    raise HTTPException(
        status_code=409,
        detail="Native maps are sealed automatically when an autonomous mapping mission completes.",
    )


@router.post("/{map_id}/load")
async def load_saved_map(map_id: str, _: str = Depends(verify_credentials)):
    """Restore an approved serialized SLAM Toolbox pose graph, read-only."""
    native_map = get_native_map_library().get(map_id)
    if native_map is None:
        raise HTTPException(status_code=404, detail="Saved Nav2 map not found")
    if not native_map.loadable:
        raise HTTPException(
            status_code=409,
            detail="Map is incomplete: its SLAM Toolbox .data file is missing.",
        )

    nav_svc = get_nav_service()
    if not await asyncio.to_thread(nav_svc.load_saved_map, map_id):
        raise HTTPException(
            status_code=503,
            detail=nav_svc.last_load_error or "Nav2/SLAM could not load the saved map.",
        )
    return {
        "status": "loaded",
        "map_id": native_map.map_id,
        "name": native_map.display_name,
        "message": "Saved map loaded and locked against new scans.",
    }


@router.delete("/{map_id}")
async def delete_saved_map(map_id: str, _: str = Depends(verify_credentials)):
    """Keep deletion out of the web UI until it can atomically remove all artifacts."""
    raise HTTPException(
        status_code=405,
        detail="Map deletion is intentionally unavailable from the web panel.",
    )

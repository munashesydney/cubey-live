"""
System health, telemetry, and authentication token endpoints.
"""

from fastapi import APIRouter, Depends
from src.config import config
from src.services.lidar_service import get_lidar_service
from src.services.mapping_service import get_mapping_service
from src.services.wheels_service import get_wheels_service
from src.services.navigation.cubey_nav_service import get_nav_service
from src.web.auth import verify_credentials

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/auth/token")
async def get_websocket_token(_: str = Depends(verify_credentials)):
    """Return WebSocket connection token for authenticated browser sessions."""
    return {"token": config.web_password or "cubey"}


@router.get("/status")
async def get_system_status(_: str = Depends(verify_credentials)):
    """Return robot battery, motion state, LiDAR telemetry, and SLAM pose."""
    lidar_svc = get_lidar_service()
    mapping_svc = get_mapping_service()
    wheels_svc = get_wheels_service()
    nav_svc = get_nav_service()

    snapshot = mapping_svc.get_snapshot()

    return {
        "status": "online",
        "battery": {
            "percentage": wheels_svc.telemetry.battery_pct,
            "voltage": wheels_svc.telemetry.battery_voltage,
            "is_charging": wheels_svc.telemetry.is_charging,
        },
        "motion": wheels_svc.telemetry.motion,
        "lidar": {
            "is_connected": lidar_svc.is_connected,
            "is_scanning": lidar_svc.is_scanning,
            "is_mock": lidar_svc.is_mock,
            "scan_rate_hz": lidar_svc.latest_scan.scan_rate_hz,
            "min_front_mm": lidar_svc.latest_scan.min_front_dist_mm,
        },
        "mapping": {
            "is_mapping": mapping_svc.is_mapping,
            "map_name": mapping_svc.map_name,
            "active_map_id": mapping_svc.active_map_id,
            "explored_cells": snapshot.total_explored_cells,
            "pose": snapshot.pose.to_dict(),
        },
        "navigation": nav_svc.telemetry.to_dict(),
    }

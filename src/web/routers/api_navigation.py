"""
SLAM mapping sessions, waypoint navigation, and teleoperation motor drive endpoints.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.services.mapping_service import get_mapping_service
from src.services.wheels_service import get_wheels_service
from src.services.navigation.cubey_nav_service import get_nav_service
from src.web.auth import verify_credentials

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["navigation"])


class DriveCommandRequest(BaseModel):
    action: str  # forward, backward, strafeLeft, strafeRight, rotateLeft, rotateRight, stop
    speed: Optional[int] = None
    duration_ms: Optional[int] = None
    continuous: bool = False


class StartMappingRequest(BaseModel):
    mode: Optional[str] = "manual"  # "manual" | "autonomous"


class NavGoalRequest(BaseModel):
    x_m: float
    y_m: float
    theta_deg: Optional[float] = 0.0


@router.post("/mapping/start")
async def start_mapping_session(req: Optional[StartMappingRequest] = None, _: str = Depends(verify_credentials)):
    """Start 2D mapping session in manual or autonomous mode."""
    nav_svc = get_nav_service()
    mode = (req.mode if req and req.mode else "manual").lower()

    if mode == "autonomous":
        started = nav_svc.start_exploration()
    else:
        started = nav_svc.start_manual_mapping()

    if not started:
        raise HTTPException(
            status_code=503,
            detail="ROS 2 Nav2/SLAM is not ready; mapping was not started.",
        )

    return {"status": "mapping_started", "mode": mode}


@router.post("/mapping/pause")
async def pause_mapping_session(_: str = Depends(verify_credentials)):
    """Pause 2D occupancy grid updates and stop autonomous motion."""
    mapping_svc = get_mapping_service()
    nav_svc = get_nav_service()
    nav_svc.stop_navigation()
    mapping_svc.pause_mapping()
    return {"status": "mapping_paused"}


@router.post("/mapping/reset")
async def reset_mapping_grid(_: str = Depends(verify_credentials)):
    """Clear the occupancy grid back to unexplored space."""
    mapping_svc = get_mapping_service()
    mapping_svc.reset_map()
    return {"status": "map_reset"}


@router.post("/navigation/goal")
async def send_navigation_goal(req: NavGoalRequest, _: str = Depends(verify_credentials)):
    """Command robot to navigate toward a 2D floorplan coordinate."""
    nav_svc = get_nav_service()
    success = nav_svc.navigate_to(x_m=req.x_m, y_m=req.y_m, theta_deg=req.theta_deg or 0.0)
    return {"status": "navigating" if success else "failed", "goal": req.dict()}


@router.post("/navigation/stop")
async def stop_navigation_command(_: str = Depends(verify_credentials)):
    """Halt active autonomous navigation or exploration."""
    nav_svc = get_nav_service()
    nav_svc.stop_navigation()
    return {"status": "navigation_stopped"}


@router.post("/control/move")
async def send_drive_command(req: DriveCommandRequest, _: str = Depends(verify_credentials)):
    """Drive Cubey's mecanum wheel base."""
    wheels_svc = get_wheels_service()
    if not wheels_svc.is_connected:
        wheels_svc.connect()

    if req.speed is not None:
        wheels_svc.set_speed(req.speed)

    logger.info(
        "HTTP Drive CMD: action=%s, speed=%s, continuous=%s (Wheels connected=%s on %s)",
        req.action,
        req.speed,
        req.continuous,
        wheels_svc.is_connected,
        wheels_svc.port,
    )

    if req.action == "stop":
        wheels_svc.stop()
    elif req.continuous:
        wheels_svc.start_continuous(req.action)
    else:
        wheels_svc.pulse(req.action, req.duration_ms or 250)

    return {"status": "command_sent", "action": req.action, "connected": wheels_svc.is_connected}


@router.post("/control/stop")
async def stop_all_motors(_: str = Depends(verify_credentials)):
    """Emergency stop for motors."""
    wheels_svc = get_wheels_service()
    logger.info("HTTP Drive CMD: Emergency STOP")
    wheels_svc.stop()
    return {"status": "stopped"}

"""
Microphone diagnostics, denoiser toggle, and test audio recording endpoints.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from src.services.audio_test_service import get_audio_test_service
from src.web.auth import verify_credentials

router = APIRouter(prefix="/api/audio", tags=["audio"])


class ToggleDenoiserRequest(BaseModel):
    enabled: bool


class RecordTestRequest(BaseModel):
    duration_s: float = 5.0


@router.get("/status")
async def get_audio_test_status(_: str = Depends(verify_credentials)):
    """Return real-time audio test diagnostics and denoiser telemetry."""
    audio_test_svc = get_audio_test_service()
    return audio_test_svc.snapshot.to_dict()


@router.post("/denoiser/toggle")
async def toggle_audio_denoiser(req: ToggleDenoiserRequest, _: str = Depends(verify_credentials)):
    """Enable or disable hardware noise suppression."""
    audio_test_svc = get_audio_test_service()
    success = audio_test_svc.set_denoiser_enabled(req.enabled)
    return {"status": "ok", "is_denoiser_enabled": req.enabled, "applied": success}


@router.post("/test_recording/start")
async def start_audio_test_recording(req: Optional[RecordTestRequest] = None, _: str = Depends(verify_credentials)):
    """Start a test clip recording for auditory playback."""
    audio_test_svc = get_audio_test_service()
    dur = req.duration_s if req else 5.0
    audio_test_svc.start_test_recording(duration_s=dur)
    return {"status": "recording_started", "duration_s": dur}


@router.get("/test_recording/{kind}")
async def download_audio_test_recording(kind: str, _: str = Depends(verify_credentials)):
    """Download or stream the recorded raw or denoised test WAV file."""
    audio_test_svc = get_audio_test_service()
    wav_bytes = audio_test_svc.get_test_wav(kind=kind)
    if not wav_bytes:
        raise HTTPException(status_code=404, detail="No test recording available yet. Click 'Record 5s Clip' first.")
    return Response(content=wav_bytes, media_type="audio/wav")

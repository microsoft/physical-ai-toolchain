"""Analysis endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.post("/trajectory-quality")
async def analyze_trajectory_quality():
    """Compute trajectory quality metrics."""
    return {"status": "not_implemented"}


@router.post("/anomaly-detection")
async def detect_anomalies():
    """Detect anomalies in episode."""
    return {"status": "not_implemented"}

"""Pydantic request/response schemas for the gateway API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CompletionRequest(BaseModel):
    prompt: str = Field(..., description="The user prompt to route through the gateway")
    team: str = "default"
    user: str = "anonymous"
    force_model: Optional[str] = Field(None, description="Bypass routing and use this model")
    optimize: bool = Field(True, description="Run token optimization before sending")
    use_cache: bool = Field(True, description="Consult the multi-level cache")


class TraceStep(BaseModel):
    stage: str
    status: str
    detail: str
    data: Dict[str, Any] = {}
    ms: float = 0.0


class CompletionResponse(BaseModel):
    id: int
    response: str
    intent: str
    served_from: str
    model: Optional[str]
    provider: Optional[str]
    cost_usd: float
    baseline_usd: float
    saved_usd: float
    latency_ms: float
    quality: float
    cache_level: Optional[str]
    blocked: bool
    trace: List[TraceStep]

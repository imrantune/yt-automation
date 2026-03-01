"""API cost tracking with real pricing from OpenAI, ElevenLabs, Minimax, Runway."""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from database.models import ApiCostLog

logger = logging.getLogger(__name__)

# ─── Real API Pricing (as of Feb 2026) ────────────────────────────────────────

PRICING = {
    "openai": {
        "gpt-4o": {"input": 2.50 / 1_000_000, "output": 10.00 / 1_000_000},
        "gpt-4o-mini": {"input": 0.15 / 1_000_000, "output": 0.60 / 1_000_000},
        "whisper-1": {"per_minute": 0.006},
        "dall-e-3-hd-1792x1024": {"per_image": 0.120},
        "dall-e-3-standard-1024x1024": {"per_image": 0.040},
    },
    "elevenlabs": {
        "per_character": 0.30 / 1_000,
    },
    "minimax": {
        # Unit-based pricing — unit_price loaded from settings (MINIMAX_UNIT_PRICE env)
        # Default: Standard package $1000/3760 units ≈ $0.266/unit
        "clips": {
            "T2V-01_720P_6s": 1.0,
            "Hailuo-02_768P_6s": 1.0,
            "Hailuo-02_768P_10s": 2.0,
            "Hailuo-02_1080P_6s": 2.0,
            "Hailuo-02_512P_6s": 0.3,
            "Hailuo-02_512P_10s": 0.5,
            "Hailuo-2.3_768P_6s": 1.0,
            "Hailuo-2.3_768P_10s": 2.0,
            "Hailuo-2.3_1080P_6s": 2.0,
        },
    },
    "runway": {
        "gen3a_turbo": {"per_second": 0.05},
    },
    "wan21": {
        # Local model — electricity only, effectively free
        "per_clip": 0.0,
    },
}


def _calc_openai_chat_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    key = model.lower()
    for model_key, rates in PRICING["openai"].items():
        if model_key in key and "input" in rates:
            return input_tokens * rates["input"] + output_tokens * rates["output"]
    return input_tokens * PRICING["openai"]["gpt-4o"]["input"] + output_tokens * PRICING["openai"]["gpt-4o"]["output"]


def _calc_elevenlabs_cost(character_count: int) -> float:
    return character_count * PRICING["elevenlabs"]["per_character"]


def _calc_whisper_cost(duration_seconds: float) -> float:
    return (duration_seconds / 60.0) * PRICING["openai"]["whisper-1"]["per_minute"]


def _calc_dalle_cost(size: str = "1792x1024", quality: str = "hd") -> float:
    if quality == "hd" and "1792" in size:
        return PRICING["openai"]["dall-e-3-hd-1792x1024"]["per_image"]
    return PRICING["openai"]["dall-e-3-standard-1024x1024"]["per_image"]


def log_openai_chat(
    session: Session,
    episode_id: int,
    job_id: int | None,
    operation: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> ApiCostLog:
    cost = _calc_openai_chat_cost(model, input_tokens, output_tokens)
    entry = ApiCostLog(
        episode_id=episode_id,
        job_id=job_id,
        service="openai",
        operation=operation,
        input_units=input_tokens,
        output_units=output_tokens,
        unit_type="tokens",
        cost_usd=round(cost, 6),
        metadata_json=json.dumps({"model": model}),
    )
    session.add(entry)
    session.flush()
    logger.debug("Cost logged: %s %s — %d in / %d out = $%.4f", "openai", operation, input_tokens, output_tokens, cost)
    return entry


def log_elevenlabs(
    session: Session,
    episode_id: int,
    job_id: int | None,
    operation: str,
    character_count: int,
) -> ApiCostLog:
    cost = _calc_elevenlabs_cost(character_count)
    entry = ApiCostLog(
        episode_id=episode_id,
        job_id=job_id,
        service="elevenlabs",
        operation=operation,
        input_units=character_count,
        output_units=0,
        unit_type="characters",
        cost_usd=round(cost, 6),
    )
    session.add(entry)
    session.flush()
    logger.debug("Cost logged: elevenlabs %s — %d chars = $%.4f", operation, character_count, cost)
    return entry


def log_whisper(
    session: Session,
    episode_id: int,
    job_id: int | None,
    operation: str,
    duration_seconds: float,
) -> ApiCostLog:
    cost = _calc_whisper_cost(duration_seconds)
    entry = ApiCostLog(
        episode_id=episode_id,
        job_id=job_id,
        service="openai",
        operation=operation,
        input_units=int(duration_seconds),
        output_units=0,
        unit_type="seconds",
        cost_usd=round(cost, 6),
        metadata_json=json.dumps({"model": "whisper-1"}),
    )
    session.add(entry)
    session.flush()
    logger.debug("Cost logged: whisper %s — %.1fs = $%.4f", operation, duration_seconds, cost)
    return entry


def log_dalle(
    session: Session,
    episode_id: int,
    job_id: int | None,
    operation: str,
    size: str = "1792x1024",
    quality: str = "hd",
) -> ApiCostLog:
    cost = _calc_dalle_cost(size, quality)
    entry = ApiCostLog(
        episode_id=episode_id,
        job_id=job_id,
        service="openai",
        operation=operation,
        input_units=1,
        output_units=0,
        unit_type="images",
        cost_usd=round(cost, 6),
        metadata_json=json.dumps({"model": "dall-e-3", "size": size, "quality": quality}),
    )
    session.add(entry)
    session.flush()
    logger.debug("Cost logged: dall-e-3 %s — 1 image = $%.4f", operation, cost)
    return entry


def log_video_provider(
    session: Session,
    episode_id: int,
    job_id: int | None,
    provider: str,
    operation: str,
    duration_seconds: float,
    model: str = "",
    resolution: str = "720P",
) -> ApiCostLog:
    if provider == "minimax":
        from config.settings import get_settings
        unit_price = get_settings(require_api_keys=False).minimax_unit_price
        clip_key = f"{model or 'T2V-01'}_{resolution}_{int(duration_seconds)}s"
        units = PRICING["minimax"]["clips"].get(clip_key, 1.0)
        cost = units * unit_price
        meta = json.dumps({"model": model or "T2V-01", "resolution": resolution, "units": units, "unit_price": round(unit_price, 4)})
        unit_type = "units"
        input_units_val = int(units * 100)
    elif provider == "runway":
        rate = PRICING["runway"]["gen3a_turbo"]["per_second"]
        cost = duration_seconds * rate
        meta = json.dumps({"model": "gen3a_turbo"})
        unit_type = "seconds"
        input_units_val = int(duration_seconds)
    elif provider == "wan21":
        cost = 0.0
        meta = json.dumps({"model": "Wan2.1-T2V-1.3B", "local": True})
        unit_type = "clips"
        input_units_val = 1
    else:
        cost = 0.0
        meta = None
        unit_type = "seconds"
        input_units_val = int(duration_seconds)

    entry = ApiCostLog(
        episode_id=episode_id,
        job_id=job_id,
        service=provider,
        operation=operation,
        input_units=input_units_val,
        output_units=0,
        unit_type=unit_type,
        cost_usd=round(cost, 6),
        metadata_json=meta,
    )
    session.add(entry)
    session.flush()
    logger.debug("Cost logged: %s %s — $%.4f", provider, operation, cost)
    return entry


def _estimate_video_costs(scene_count: int, current_provider: str | None) -> dict | None:
    """Estimate costs for all providers to enable comparison."""
    if scene_count == 0:
        return None

    from config.settings import get_settings
    unit_price = get_settings(require_api_keys=False).minimax_unit_price
    minimax_cost = scene_count * 1.0 * unit_price
    runway_cost = scene_count * 6.0 * PRICING["runway"]["gen3a_turbo"]["per_second"]
    wan21_cost = 0.0

    providers = [
        {"provider": "wan21", "estimated_cost": round(wan21_cost, 4), "is_current": current_provider == "wan21", "note": "Local (free)"},
        {"provider": "minimax", "estimated_cost": round(minimax_cost, 4), "is_current": current_provider == "minimax", "note": f"{scene_count} x 1 unit @ ${unit_price:.4f}"},
        {"provider": "runway", "estimated_cost": round(runway_cost, 4), "is_current": current_provider == "runway", "note": f"{scene_count} x 6s @ $0.05/s"},
    ]

    current_cost = next((p["estimated_cost"] for p in providers if p["is_current"]), 0.0)
    savings = round(current_cost - wan21_cost, 4) if current_provider != "wan21" and current_cost > 0 else None

    return {
        "scene_count": scene_count,
        "providers": providers,
        "savings": savings,
    }


def get_episode_costs(session: Session, episode_id: int) -> dict:
    """Get cost summary for an episode with provider comparison."""
    logs = session.query(ApiCostLog).filter(ApiCostLog.episode_id == episode_id).all()
    by_service: dict[str, float] = {}
    total = 0.0
    details: list[dict] = []
    video_clips = 0
    video_provider_used = None
    for log in logs:
        by_service[log.service] = by_service.get(log.service, 0.0) + log.cost_usd
        total += log.cost_usd
        details.append({
            "service": log.service,
            "operation": log.operation,
            "input_units": log.input_units,
            "output_units": log.output_units,
            "unit_type": log.unit_type,
            "cost_usd": log.cost_usd,
            "created_at": log.created_at.isoformat() if log.created_at else "",
        })
        if "clip" in log.operation:
            video_clips += 1
            video_provider_used = log.service

    from sqlalchemy import func, select
    from database.models import Scene
    scene_count = session.query(func.count()).select_from(Scene).filter(Scene.episode_id == episode_id).scalar() or 0
    if video_clips == 0:
        video_clips = scene_count

    video_comparison = _estimate_video_costs(video_clips or scene_count, video_provider_used)

    return {
        "episode_id": episode_id,
        "total_usd": round(total, 4),
        "by_service": {k: round(v, 4) for k, v in by_service.items()},
        "details": details,
        "video_comparison": video_comparison,
    }

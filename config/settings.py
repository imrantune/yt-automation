"""Application settings loaded from environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT_DIR / ".env"
load_dotenv(ENV_PATH if ENV_PATH.exists() else None)

logger = logging.getLogger(__name__)

VALID_PROVIDERS = {"wan21", "minimax", "runway"}


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: str | None, default: int, name: str = "unknown") -> int:
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"Expected integer for {name}, got '{value}'") from exc


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the Spartacus automation pipeline."""

    app_env: str
    log_level: str

    database_url: str
    redis_url: str

    openai_api_key: str
    openai_model: str

    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    elevenlabs_model_id: str

    minimax_api_key: str
    minimax_api_base: str
    minimax_enabled: bool

    runway_api_key: str
    runway_api_base: str
    runway_enabled: bool

    wan21_model_id: str
    wan21_device: str
    wan21_enabled: bool
    wan21_max_frames: int

    video_provider_order: tuple[str, ...]
    video_resolution: str
    video_fps: int
    video_codec: str
    video_bitrate: str
    audio_codec: str
    audio_bitrate: str
    default_scene_duration_seconds: int

    output_root: Path
    scripts_dir: Path
    audio_dir: Path
    clips_dir: Path
    final_dir: Path
    temp_dir: Path

    @classmethod
    def from_env(cls, require_api_keys: bool = True) -> "Settings":
        """Build settings object and validate required values.

        Set require_api_keys=False for infra-only contexts (migrations, seeding).
        """
        output_root = Path(os.getenv("OUTPUT_ROOT", str(ROOT_DIR / "output"))).expanduser()
        scripts_dir = output_root / "scripts"
        audio_dir = output_root / "audio"
        clips_dir = output_root / "clips"
        final_dir = output_root / "final"
        temp_dir = output_root / "temp"

        provider_order_env = os.getenv("VIDEO_PROVIDER_ORDER", "wan21,minimax,runway")
        provider_order = tuple(
            item.strip().lower()
            for item in provider_order_env.split(",")
            if item.strip()
        )
        if not provider_order:
            raise ValueError("VIDEO_PROVIDER_ORDER must contain at least one provider.")
        invalid = set(provider_order) - VALID_PROVIDERS
        if invalid:
            raise ValueError(f"Invalid providers in VIDEO_PROVIDER_ORDER: {invalid}. Valid: {VALID_PROVIDERS}")

        database_url = os.getenv("DATABASE_URL", "").strip()
        redis_url = os.getenv("REDIS_URL", "").strip()
        openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        elevenlabs_voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip()

        required = {"DATABASE_URL": database_url, "REDIS_URL": redis_url}
        if require_api_keys:
            required.update({
                "OPENAI_API_KEY": openai_api_key,
                "ELEVENLABS_API_KEY": elevenlabs_api_key,
                "ELEVENLABS_VOICE_ID": elevenlabs_voice_id,
            })

        missing_required = [name for name, value in required.items() if not value]
        if missing_required:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_required)}")

        return cls(
            app_env=os.getenv("APP_ENV", "development"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            database_url=database_url,
            redis_url=redis_url,
            openai_api_key=openai_api_key,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            elevenlabs_api_key=elevenlabs_api_key,
            elevenlabs_voice_id=elevenlabs_voice_id,
            elevenlabs_model_id=os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2"),
            minimax_api_key=os.getenv("MINIMAX_API_KEY", "").strip(),
            minimax_api_base=os.getenv("MINIMAX_API_BASE", "https://api.minimax.chat"),
            minimax_enabled=_to_bool(os.getenv("MINIMAX_ENABLED"), default=True),
            runway_api_key=os.getenv("RUNWAY_API_KEY", "").strip(),
            runway_api_base=os.getenv("RUNWAY_API_BASE", "https://api.dev.runwayml.com"),
            runway_enabled=_to_bool(os.getenv("RUNWAY_ENABLED"), default=True),
            wan21_model_id=os.getenv("WAN21_MODEL_ID", "Wan-AI/Wan2.1-T2V-1.3B"),
            wan21_device=os.getenv("WAN21_DEVICE", "mps"),
            wan21_enabled=_to_bool(os.getenv("WAN21_ENABLED"), default=True),
            wan21_max_frames=_to_int(os.getenv("WAN21_MAX_FRAMES"), default=49, name="WAN21_MAX_FRAMES"),
            video_provider_order=provider_order,
            video_resolution=os.getenv("VIDEO_RESOLUTION", "1920x1080"),
            video_fps=_to_int(os.getenv("VIDEO_FPS"), default=24, name="VIDEO_FPS"),
            video_codec=os.getenv("VIDEO_CODEC", "libx264"),
            video_bitrate=os.getenv("VIDEO_BITRATE", "5000k"),
            audio_codec=os.getenv("AUDIO_CODEC", "aac"),
            audio_bitrate=os.getenv("AUDIO_BITRATE", "192k"),
            default_scene_duration_seconds=_to_int(
                os.getenv("DEFAULT_SCENE_DURATION_SECONDS"), default=45, name="DEFAULT_SCENE_DURATION_SECONDS"
            ),
            output_root=output_root,
            scripts_dir=scripts_dir,
            audio_dir=audio_dir,
            clips_dir=clips_dir,
            final_dir=final_dir,
            temp_dir=temp_dir,
        )

    def ensure_paths(self) -> None:
        """Create required output directories if missing."""
        for path in (
            self.output_root,
            self.scripts_dir,
            self.audio_dir,
            self.clips_dir,
            self.final_dir,
            self.temp_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


_cached_settings: Settings | None = None


def get_settings(require_api_keys: bool = True) -> Settings:
    """Get settings, creating and caching on first call.

    The first invocation determines the validation level. Subsequent calls
    return the cached instance regardless of the flag value.
    """
    global _cached_settings  # noqa: PLW0603
    if _cached_settings is not None:
        return _cached_settings
    settings = Settings.from_env(require_api_keys=require_api_keys)
    settings.ensure_paths()
    _cached_settings = settings
    return settings

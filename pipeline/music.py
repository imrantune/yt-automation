"""Background music mixing for episode scenes."""

from __future__ import annotations

import logging
import random
from pathlib import Path

from config.settings import get_settings
from database.models import SceneType
from pipeline.video_generator import _run_ffmpeg


logger = logging.getLogger(__name__)
settings = get_settings()

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets" / "music"

SCENE_MUSIC_MAP: dict[SceneType, str] = {
    SceneType.INTRO: "tension",
    SceneType.FIGHT1: "battle",
    SceneType.FIGHT2: "battle",
    SceneType.CLIMAX: "epic",
    SceneType.OUTRO: "calm",
    SceneType.OTHER: "tension",
}


def _find_track(mood: str) -> Path | None:
    """Find a music track matching the mood from assets/music/{mood}/ directory."""
    mood_dir = ASSETS_DIR / mood
    if not mood_dir.is_dir():
        return None
    tracks = list(mood_dir.glob("*.mp3")) + list(mood_dir.glob("*.wav"))
    if not tracks:
        return None
    return random.choice(tracks)


def mix_music_with_audio(
    narration_path: Path,
    scene_type: SceneType,
    output_path: Path,
    music_volume_db: float = -18.0,
) -> Path:
    """Mix background music under narration audio.

    Returns the output path (mixed file) or the original narration if no music found.
    """
    mood = SCENE_MUSIC_MAP.get(scene_type, "tension")
    track = _find_track(mood)
    if track is None:
        logger.debug("No music track for mood '%s', using narration only.", mood)
        return narration_path

    _run_ffmpeg([
        "ffmpeg", "-y",
        "-i", str(narration_path),
        "-stream_loop", "-1", "-i", str(track),
        "-filter_complex",
        f"[1:a]volume={music_volume_db}dB[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[out]",
        "-map", "[out]",
        "-c:a", "libmp3lame", "-b:a", settings.audio_bitrate,
        str(output_path),
    ], f"mix music ({mood})")

    return output_path


def mix_episode_music(episode_number: int, scenes_with_audio: list) -> list[Path]:
    """Mix background music into all scene audio files. Returns list of mixed audio paths."""
    mixed_dir = settings.output_root / "mixed_audio"
    mixed_dir.mkdir(parents=True, exist_ok=True)

    result_paths: list[Path] = []
    for scene in scenes_with_audio:
        if not scene.audio_file_path:
            continue
        narration = Path(scene.audio_file_path)
        if not narration.exists():
            result_paths.append(narration)
            continue
        mixed_path = mixed_dir / f"episode_{episode_number:04d}_scene_{scene.scene_order:02d}_mixed.mp3"
        actual = mix_music_with_audio(narration, scene.scene_type, mixed_path)
        result_paths.append(actual)

    return result_paths

"""Sound effects layering for gladiator arena scenes.

Generates procedural SFX via FFmpeg when asset files are missing,
and mixes them into the scene audio at appropriate positions.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path

from config.settings import get_settings
from database.models import SceneType
from pipeline.video_generator import _probe_duration, _run_ffmpeg


logger = logging.getLogger(__name__)
settings = get_settings()

SFX_DIR = Path(__file__).resolve().parents[1] / "assets" / "sfx"

SFX_CATEGORIES = ["sword_clash", "crowd_roar", "crowd_cheer", "impact", "horn", "gate", "footsteps"]

SCENE_SFX_MAP: dict[SceneType, list[tuple[str, float, float]]] = {
    SceneType.INTRO: [
        ("horn", 0.0, -8.0),
        ("crowd_roar", 2.0, -14.0),
        ("gate", 4.0, -10.0),
    ],
    SceneType.FIGHT1: [
        ("sword_clash", 1.5, -10.0),
        ("crowd_roar", 3.0, -16.0),
        ("impact", 5.0, -8.0),
        ("sword_clash", 7.0, -10.0),
    ],
    SceneType.FIGHT2: [
        ("impact", 1.0, -8.0),
        ("sword_clash", 2.5, -10.0),
        ("crowd_cheer", 4.0, -14.0),
        ("sword_clash", 6.0, -10.0),
        ("impact", 8.0, -8.0),
    ],
    SceneType.CLIMAX: [
        ("impact", 0.5, -6.0),
        ("sword_clash", 2.0, -8.0),
        ("crowd_roar", 3.5, -10.0),
        ("impact", 5.0, -6.0),
        ("crowd_cheer", 7.0, -10.0),
        ("horn", 9.0, -8.0),
    ],
    SceneType.OUTRO: [
        ("crowd_cheer", 1.0, -16.0),
    ],
    SceneType.OTHER: [
        ("crowd_roar", 2.0, -18.0),
    ],
}


def _ensure_sfx_dirs() -> None:
    """Create SFX subdirectories if they don't exist."""
    for cat in SFX_CATEGORIES:
        (SFX_DIR / cat).mkdir(parents=True, exist_ok=True)


def _generate_procedural_sfx(category: str, output_path: Path, duration: float = 1.5) -> None:
    """Generate a basic SFX file using FFmpeg synthesis when no asset file exists."""
    filters: dict[str, str] = {
        "sword_clash": (
            "aevalsrc=exprs='0.8*sin(3500*2*PI*t)*exp(-15*t)+0.4*random(0)*(1-t/0.3)'"
            f":d={duration}:s=44100,highpass=f=800,lowpass=f=8000"
        ),
        "crowd_roar": (
            f"anoisesrc=d={duration}:c=brown:r=44100:a=0.5,"
            "lowpass=f=2000,highpass=f=200"
        ),
        "crowd_cheer": (
            f"anoisesrc=d={duration}:c=pink:r=44100:a=0.4,"
            "lowpass=f=4000,highpass=f=300,tremolo=f=6:d=0.4"
        ),
        "impact": (
            "aevalsrc=exprs='0.9*sin(80*2*PI*t)*exp(-8*t)+0.5*random(0)*exp(-20*t)'"
            f":d={duration}:s=44100,lowpass=f=500"
        ),
        "horn": (
            "aevalsrc=exprs='0.6*sin(220*2*PI*t)+0.3*sin(440*2*PI*t)'"
            f":d={duration}:s=44100,afade=t=in:d=0.1,afade=t=out:st={duration - 0.3}:d=0.3"
        ),
        "gate": (
            "aevalsrc=exprs='0.7*sin(150*2*PI*t)*exp(-3*t)+0.3*random(0)*exp(-5*t)'"
            f":d={duration}:s=44100,lowpass=f=1000"
        ),
        "footsteps": (
            "aevalsrc=exprs='0.5*sin(200*2*PI*t)*exp(-30*t)*((1+sin(8*2*PI*t))/2)'"
            f":d={duration}:s=44100,highpass=f=100,lowpass=f=3000"
        ),
    }

    af = filters.get(category, f"anoisesrc=d={duration}:c=white:r=44100:a=0.3")
    _run_ffmpeg([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", af,
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(output_path),
    ], f"generate sfx {category}")


def _find_sfx(category: str) -> Path:
    """Find an SFX file for the category, generating one procedurally if missing."""
    _ensure_sfx_dirs()
    cat_dir = SFX_DIR / category
    tracks = list(cat_dir.glob("*.mp3")) + list(cat_dir.glob("*.wav"))
    if tracks:
        return random.choice(tracks)

    generated = cat_dir / f"{category}_generated.mp3"
    if not generated.exists():
        dur_map = {"sword_clash": 0.5, "impact": 0.8, "horn": 2.5, "gate": 2.0,
                   "crowd_roar": 3.0, "crowd_cheer": 3.0, "footsteps": 2.0}
        _generate_procedural_sfx(category, generated, duration=dur_map.get(category, 1.5))
        logger.info("Generated procedural SFX: %s", generated)
    return generated


def mix_sfx_into_audio(
    narration_path: Path,
    scene_type: SceneType,
    output_path: Path,
    sfx_volume_offset_db: float = 0.0,
) -> Path:
    """Layer sound effects onto a narration/music audio track.

    Returns the output path (mixed file) or the original if no SFX defined for this scene type.
    """
    sfx_plan = SCENE_SFX_MAP.get(scene_type, [])
    if not sfx_plan:
        return narration_path

    audio_dur = _probe_duration(narration_path)
    if audio_dur <= 0:
        return narration_path

    valid_sfx = [(cat, offset, vol) for cat, offset, vol in sfx_plan if offset < audio_dur]
    if not valid_sfx:
        return narration_path

    inputs = ["-i", str(narration_path.resolve())]
    for cat, _, _ in valid_sfx:
        sfx_file = _find_sfx(cat)
        inputs.extend(["-i", str(sfx_file.resolve())])

    n_inputs = len(valid_sfx) + 1
    filter_parts: list[str] = []
    mix_labels: list[str] = ["[0:a]"]

    for i, (cat, offset, vol) in enumerate(valid_sfx, start=1):
        actual_vol = vol + sfx_volume_offset_db
        label = f"[sfx{i}]"
        filter_parts.append(
            f"[{i}:a]adelay={int(offset * 1000)}|{int(offset * 1000)},"
            f"volume={actual_vol}dB,apad=whole_dur={audio_dur}{label}"
        )
        mix_labels.append(label)

    mix_inputs = "".join(mix_labels)
    filter_parts.append(
        f"{mix_inputs}amix=inputs={n_inputs}:duration=first:dropout_transition=2[out]"
    )

    filter_complex = ";".join(filter_parts)

    _run_ffmpeg([
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:a", "libmp3lame", "-b:a", settings.audio_bitrate,
        "-t", str(audio_dur),
        str(output_path.resolve()),
    ], f"mix sfx ({scene_type.value})")

    return output_path


def mix_episode_sfx(episode_number: int, scenes_with_audio: list, audio_paths: list[Path]) -> list[Path]:
    """Layer SFX onto all scene audio files. Returns list of final audio paths.

    Takes the already-music-mixed audio paths and adds SFX on top.
    """
    sfx_dir = settings.output_root / "sfx_audio"
    sfx_dir.mkdir(parents=True, exist_ok=True)

    result_paths: list[Path] = []
    for i, scene in enumerate(scenes_with_audio):
        if i >= len(audio_paths):
            break
        src_audio = audio_paths[i]
        if not src_audio.exists():
            result_paths.append(src_audio)
            continue

        sfx_path = sfx_dir / f"episode_{episode_number:04d}_scene_{scene.scene_order:02d}_sfx.mp3"
        actual = mix_sfx_into_audio(src_audio, scene.scene_type, sfx_path)
        result_paths.append(actual)

    return result_paths

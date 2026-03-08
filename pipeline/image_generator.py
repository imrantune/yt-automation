"""Scene image generation via DALL-E 3 or Midjourney with character-consistent prompts."""

from __future__ import annotations

import logging
import time
from io import BytesIO
from pathlib import Path

import requests
from openai import OpenAI
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.models import Character, Episode, Scene, SceneType


logger = logging.getLogger(__name__)
settings = get_settings()

IMAGES_DIR = settings.output_root / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

CHARACTER_VISUAL_TRAITS: dict[str, str] = {
    "Spartacus": (
        "muscular Thracian gladiator with short dark hair and a scar across his left cheek, "
        "wearing bronze pauldrons and leather chest guard, dramatic pose"
    ),
    "Crixus": (
        "massive bald Gallic gladiator with a thick black beard and tribal tattoos on his arms, "
        "round bronze shield, heavy iron chest plate, imposing stance"
    ),
    "Gannicus": (
        "lean athletic Celtic warrior with flowing sandy-blond hair and blue face paint, "
        "light leather armor, confident stance"
    ),
    "Oenomaus": (
        "tall dark-skinned African gladiator with a shaved head and gold ear cuffs, "
        "heavy bronze lamellar armor, stoic expression"
    ),
    "Agron": (
        "young Germanic warrior with short reddish-brown hair and a fur-lined cloak, "
        "chainmail vest, determined gaze"
    ),
}

SCENE_TYPE_FRAMING: dict[SceneType, str] = {
    SceneType.INTRO: "wide establishing shot of a theatrical Roman colosseum at sunset with torches lit, celebratory atmosphere",
    SceneType.FIGHT1: "dramatic theatrical performance of two armored figures facing each other, cinematic posing, professional stage combat style",
    SceneType.FIGHT2: "artistic mid-shot of performers in ancient armor, shield-to-shield dramatic stance, theatrical shadows, heroic expressions",
    SceneType.CLIMAX: "majestic low-angle hero shot in golden light, triumphant artistic pose, celebratory lighting, peaceful victory",
    SceneType.OUTRO: "wide shot of the empty arena at dusk, glowing torches, peaceful smoke, cinematic historical atmosphere",
    SceneType.OTHER: "cinematic artistic scenery inside a historical amphitheater with atmospheric lighting",
}

BASE_STYLE = (
    "Hyper-detailed digital painting, cinematic composition, dramatic chiaroscuro lighting, "
    "warm golden tones, historical theater aesthetic, 16:9 aspect ratio, "
    "dark moody atmosphere with dust particles in shafts of light, "
    "style of concept art for a historical drama. "
    "NO text, NO watermarks, NO modern elements. "
    "Artistic theatrical performance, safe for all audiences, historical reenactment aesthetic."
)

SAFETY_INSTRUCTION = (
    "IMPORTANT: Generate a safe, theatrical, PG-rated historical drama scene. "
    "Focus on atmospheric lighting, cinematic composition, and expressive posing. "
    "Do not include any weapons in use, no combat, no injuries, no blood, and no aggression. "
    "The scenery and armor are for artistic historical reenactment only. "
)


def _get_character_descriptions(session: Session, narration: str) -> str:
    """Extract visual descriptions for characters mentioned in the narration."""
    characters = list(
        session.execute(select(Character).where(Character.is_alive.is_(True))).scalars()
    )
    mentioned: list[str] = []
    narration_lower = narration.lower()
    for char in characters:
        if char.name.lower() in narration_lower:
            traits = CHARACTER_VISUAL_TRAITS.get(char.name, f"{char.name}, a {char.origin} gladiator")
            mentioned.append(traits)

    if not mentioned:
        return "generic Roman gladiators in bronze armor with helmets"
    return "; ".join(mentioned[:3])


def sanitize_for_dalle(text: str, max_len: int = 300) -> str:
    """Reduce content-policy triggers: violence, blood, weapons-in-action, death."""
    t = text[:max_len].lower()
    for old, new in [
        ("blood", "dust"),
        ("gore", "sand"),
        ("bloody", "dusty"),
        ("kill", "surpass"),
        ("killed", "overcome"),
        ("dying", "fading"),
        ("death", "conclusion"),
        ("dead", "fallen"),
        ("strike", "movement"),
        ("struck", "hit"),
        ("wound", "mark"),
        ("wounded", "marked"),
        ("sword strike", "artistic move"),
        ("clash", "meet"),
        ("slaughter", "contest"),
        ("brutal", "intense"),
        ("violent", "dynamic"),
        ("weapon", "theatrical tool"),
        ("sword", "theatrical blade"),
        ("gladius", "blunt stage blade"),
        ("spear", "ceremonial staff"),
        ("dagger", "prop blade"),
        ("stab", "gesture"),
        ("pierce", "touch"),
        ("pain", "effort"),
        ("agony", "tension"),
        ("scream", "shout"),
        ("torture", "hardship"),
        ("execution", "exit"),
        ("behead", "overcome"),
        ("murder", "defeat"),
        ("fight", "choreographed contest"),
        ("battle", "theatrical performance"),
        ("warrior", "heroic figure"),
        ("combat", "stage action"),
        ("attack", "advance"),
        ("opponent", "rival performer"),
        ("adversary", "challenger"),
        ("enemy", "rival"),
        ("aggressive", "passionate"),
        ("assault", "sequence"),
        ("thrust", "motion"),
        ("blade", "parade blade"),
        ("war", "history"),
    ]:
        t = t.replace(old, new)
    return t


def build_scene_image_prompt(
    session: Session,
    episode: Episode,
    scene: Scene,
) -> str:
    """Build a DALL-E prompt with character consistency and scene-type framing (content-policy safe)."""
    safe_narration = sanitize_for_dalle(scene.narration_text)
    framing = SCENE_TYPE_FRAMING.get(scene.scene_type, SCENE_TYPE_FRAMING[SceneType.OTHER])
    char_desc = _get_character_descriptions(session, scene.narration_text)

    return (
        f"{SAFETY_INSTRUCTION} "
        f"{framing}. "
        f"Characters: {char_desc}. "
        f"Scene context: {safe_narration}. "
        f"{BASE_STYLE}"
    )


def _generate_dalle_image(prompt: str, output_path: Path, quality: str = "hd", size: str = "1792x1024", is_short: bool = False) -> Path:
    """Generate a single image via DALL-E 3 API."""
    from pipeline.retry import retry_api_call

    @retry_api_call(max_retries=3, base_delay=5.0)
    def _call_dalle():
        client = OpenAI(api_key=settings.openai_api_key)
        resp = client.images.generate(
            model="dall-e-3",
            prompt=prompt[:4000],
            size=size,
            quality=quality,
            n=1,
        )
        url = resp.data[0].url
        img_resp = requests.get(url, timeout=60)
        img_resp.raise_for_status()
        return img_resp.content

    img_bytes = _call_dalle()
    img = Image.open(BytesIO(img_bytes)).convert("RGB")
    w, h = (int(x) for x in settings.video_resolution.split("x"))
    if is_short:
        w, h = 1080, 1920
    img = img.resize((w, h), Image.Resampling.LANCZOS)
    img.save(str(output_path), "PNG", optimize=True)
    logger.info("DALL-E image saved: %s", output_path)
    return output_path


def _generate_midjourney_image(prompt: str, output_path: Path, is_short: bool = False) -> Path:
    """Generate a single image via Midjourney proxy API (GoAPI-compatible).

    Flow: POST /mj/v2/imagine -> poll task -> download upscaled image -> resize.
    """
    from pipeline.retry import retry_api_call

    api_key = settings.midjourney_api_key
    base = settings.midjourney_api_base.rstrip("/")
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}

    ar_tag = "--ar 9:16" if is_short else "--ar 16:9"
    mj_prompt = prompt[:2000] + f" {ar_tag} --style raw --v 6.1"

    @retry_api_call(max_retries=3, base_delay=5.0)
    def _submit_imagine():
        resp = requests.post(
            f"{base}/mj/v2/imagine",
            headers=headers,
            json={"prompt": mj_prompt},
            timeout=120,
        )
        resp.raise_for_status()
        return resp

    create_res = _submit_imagine()
    task_data = create_res.json()
    task_id = task_data.get("task_id") or task_data.get("taskId")
    if not task_id:
        raise RuntimeError(f"Midjourney API returned no task_id: {task_data}")
    logger.info("Midjourney task created: %s", task_id)

    image_url: str | None = None
    for attempt in range(90):
        time.sleep(4)
        poll_res = requests.get(
            f"{base}/mj/v2/fetch",
            headers=headers,
            params={"task_id": task_id},
            timeout=60,
        )
        poll_res.raise_for_status()
        poll_data = poll_res.json()
        status = str(poll_data.get("status", "")).lower()
        logger.debug("Midjourney %s poll #%d: status=%s", task_id, attempt + 1, status)

        if status in ("finished", "completed", "success"):
            image_url = (
                poll_data.get("task_result", {}).get("image_url")
                or poll_data.get("image_url")
                or poll_data.get("output", {}).get("image_url")
            )
            if not image_url:
                discord_url = poll_data.get("task_result", {}).get("discord_image_url")
                if discord_url:
                    image_url = discord_url
            break
        if status in ("failed", "cancelled", "error"):
            error_msg = poll_data.get("message") or poll_data.get("error") or status
            raise RuntimeError(f"Midjourney task {task_id} failed: {error_msg}")

    if not image_url:
        raise RuntimeError(f"Midjourney task {task_id} timed out after 6 minutes.")

    img_response = requests.get(image_url, timeout=120)
    img_response.raise_for_status()

    img = Image.open(BytesIO(img_response.content)).convert("RGB")
    w, h = (int(x) for x in settings.video_resolution.split("x"))
    if is_short:
        w, h = 1080, 1920
    img = img.resize((w, h), Image.Resampling.LANCZOS)
    img.save(str(output_path), "PNG", optimize=True)
    logger.info("Midjourney image saved: %s (task %s)", output_path, task_id)
    return output_path


def get_active_image_provider() -> str:
    """Return 'dall-e' or 'midjourney' based on config and availability."""
    from config.settings import get_settings as gs
    s = gs(require_api_keys=False)
    if s.image_provider == "midjourney" and s.midjourney_enabled and s.midjourney_api_key:
        return "midjourney"
    return "dall-e"


def generate_scene_image(
    session: Session,
    episode: Episode,
    scene: Scene,
    quality: str = "hd",
    size: str = "1792x1024",
) -> Path:
    """Generate a single scene image using the active image provider (DALL-E or Midjourney)."""
    prompt = build_scene_image_prompt(session, episode, scene)
    image_path = IMAGES_DIR / f"episode_{episode.episode_number:04d}_scene_{scene.scene_order:02d}.png"
    provider = get_active_image_provider()

    if provider == "midjourney":
        _generate_midjourney_image(prompt, image_path, is_short=episode.is_short)
    else:
        actual_size = "1024x1792" if episode.is_short else size
        _generate_dalle_image(prompt, image_path, quality=quality, size=actual_size, is_short=episode.is_short)

    logger.info("Scene image [%s] saved: %s (prompt: %.80s…)", provider, image_path, prompt)
    return image_path


def generate_episode_images(
    session: Session,
    job_id: int,
    episode: Episode,
    scenes: list[Scene] | None = None,
) -> list[Path]:
    """Generate scene images for all scenes using active provider (DALL-E or Midjourney).

    Returns list of image paths in scene order.
    """
    from database.connection import log_job_step, log_job_step_isolated
    from database.models import StepStatus

    img_provider = get_active_image_provider()
    provider_label = "Midjourney" if img_provider == "midjourney" else "DALL-E"

    try:
        log_job_step(session, job_id, "image_gen", StepStatus.STARTED,
                     f"Generating {provider_label} scene images.")

        if scenes is None:
            scenes = list(
                session.execute(
                    select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.scene_order.asc())
                ).scalars()
            )

        image_paths: list[Path] = []
        for scene in scenes:
            img_path = generate_scene_image(session, episode, scene)
            image_paths.append(img_path)

            from pipeline.cost_tracker import log_dalle, log_midjourney
            if img_provider == "midjourney":
                log_midjourney(session, episode.id, job_id, f"scene_{scene.scene_order}_image")
            else:
                actual_size = "1024x1792" if episode.is_short else "1792x1024"
                log_dalle(session, episode.id, job_id, f"scene_{scene.scene_order}_image",
                          size=actual_size, quality="hd")
            session.flush()

        log_job_step(
            session, job_id, "image_gen", StepStatus.SUCCESS,
            f"Generated {len(image_paths)} {provider_label} images for episode {episode.episode_number}.",
        )
        return image_paths
    except Exception as exc:
        log_job_step_isolated(job_id, "image_gen", StepStatus.FAILED, f"Image generation failed: {exc}")
        logger.exception("Image generation failed for episode_id=%s", episode.id)
        raise

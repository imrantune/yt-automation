"""YouTube Data API v3 video uploader with playlist and chapter support."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.connection import log_job_step, log_job_step_isolated
from database.models import Episode, EpisodeSEO, EpisodeStatus, Scene, Short, StepStatus


logger = logging.getLogger(__name__)
settings = get_settings()


class YouTubeUploader:
    """Upload videos and shorts to YouTube via Data API v3."""

    def __init__(self) -> None:
        self.client_secret_path = os.getenv("YOUTUBE_CLIENT_SECRET_PATH", "")
        self.credentials_path = os.getenv("YOUTUBE_CREDENTIALS_PATH", "youtube_credentials.json")
        self.privacy = os.getenv("YOUTUBE_UPLOAD_PRIVACY", "unlisted")
        self.playlist_name = os.getenv("YOUTUBE_PLAYLIST_NAME", "Spartacus Arena Season 1")
        self._service = None

    def _ensure_service(self):
        """Authenticate and build YouTube API service."""
        if self._service is not None:
            return self._service

        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError(
                "YouTube upload requires: pip install google-api-python-client google-auth-oauthlib"
            ) from exc

        SCOPES = [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube",
        ]

        creds = None
        creds_file = Path(self.credentials_path)
        if creds_file.exists():
            creds = Credentials.from_authorized_user_file(str(creds_file), SCOPES)

        if creds and creds.expired and creds.refresh_token:
            try:
                from google.auth.transport.requests import Request as AuthRequest
                creds.refresh(AuthRequest())
                creds_file.write_text(creds.to_json())
                logger.info("YouTube credentials refreshed successfully.")
            except Exception as refresh_exc:
                logger.warning("Credential refresh failed (%s), re-authenticating.", refresh_exc)
                creds = None

        if not creds or not creds.valid:
            if not self.client_secret_path or not Path(self.client_secret_path).exists():
                raise RuntimeError(
                    f"YouTube client secret not found at '{self.client_secret_path}'. "
                    "Set YOUTUBE_CLIENT_SECRET_PATH in .env and download it from "
                    "Google Cloud Console > APIs & Credentials > OAuth 2.0 Client IDs."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                self.client_secret_path,
                scopes=SCOPES,
            )
            creds = flow.run_local_server(port=0)
            creds_file.write_text(creds.to_json())
            logger.info("YouTube credentials saved to %s", creds_file)

        self._service = build("youtube", "v3", credentials=creds)
        return self._service

    def _get_or_create_playlist(self) -> str | None:
        """Find existing playlist or create one. Returns playlist ID."""
        try:
            service = self._ensure_service()
            response = service.playlists().list(part="snippet", mine=True, maxResults=50).execute()
            for item in response.get("items", []):
                if item["snippet"]["title"] == self.playlist_name:
                    return item["id"]

            body = {
                "snippet": {
                    "title": self.playlist_name,
                    "description": "AI-generated Spartacus gladiator arena episodes. New episode every day.",
                },
                "status": {"privacyStatus": self.privacy},
            }
            result = service.playlists().insert(part="snippet,status", body=body).execute()
            logger.info("Created playlist '%s' (%s)", self.playlist_name, result["id"])
            return result["id"]
        except Exception:
            logger.warning("Could not get/create playlist, skipping playlist add.")
            return None

    def _add_to_playlist(self, video_id: str, playlist_id: str) -> None:
        """Add a video to a playlist."""
        try:
            service = self._ensure_service()
            service.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()
            logger.info("Added video %s to playlist %s", video_id, playlist_id)
        except Exception:
            logger.warning("Failed to add video %s to playlist %s", video_id, playlist_id)

    def _build_chapters_description(self, session: Session, episode: Episode, base_description: str) -> str:
        """Auto-generate YouTube chapters/timestamps from scene data."""
        scenes = list(
            session.execute(
                select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.scene_order.asc())
            ).scalars()
        )
        if not scenes:
            return base_description

        chapters: list[str] = ["0:00 Intro"]
        scene_type_labels = {
            "intro": "Introduction",
            "fight1": "First Battle",
            "fight2": "Second Battle",
            "climax": "The Climax",
            "outro": "Aftermath",
        }

        offset_seconds = 5
        for scene in scenes:
            if scene.audio_file_path and Path(scene.audio_file_path).exists():
                from pipeline.video_generator import _probe_duration
                dur = _probe_duration(Path(scene.audio_file_path))
            else:
                dur = settings.default_scene_duration_seconds

            label = scene_type_labels.get(scene.scene_type.value, f"Scene {scene.scene_order}")
            minutes = int(offset_seconds // 60)
            secs = int(offset_seconds % 60)
            chapters.append(f"{minutes}:{secs:02d} {label}")
            offset_seconds += dur

        chapters_text = "\n".join(chapters)
        return f"{base_description}\n\n📋 Chapters:\n{chapters_text}"

    def upload_video(
        self,
        session: Session,
        job_id: int,
        episode: Episode,
        video_path: Path,
        thumbnail_path: Path | None = None,
        seo: EpisodeSEO | None = None,
    ) -> str:
        """Upload video to YouTube with playlist, chapters, and thumbnail."""
        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            raise RuntimeError("google-api-python-client required for upload") from exc

        try:
            log_job_step(session, job_id, "youtube_upload", StepStatus.STARTED, "Uploading to YouTube.")
            service = self._ensure_service()

            title = seo.title_seo if seo else episode.title
            description = seo.description_seo if seo else episode.description
            tags = seo.tags if seo else []

            if not description.strip().startswith("This video was created using AI"):
                description = (
                    "This video was created using AI-generated narration and visuals. "
                    "All characters and events are fictional.\n\n" + description
                )

            description = self._build_chapters_description(session, episode, description)

            body = {
                "snippet": {
                    "title": title[:100],
                    "description": description,
                    "tags": tags[:30],
                    "categoryId": "24",
                },
                "status": {
                    "privacyStatus": self.privacy,
                    "selfDeclaredMadeForKids": False,
                },
            }

            media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
            request = service.videos().insert(part="snippet,status", body=body, media_body=media)

            response = None
            while response is None:
                _, response = request.next_chunk()

            video_id = response["id"]
            youtube_url = f"https://www.youtube.com/watch?v={video_id}"

            if thumbnail_path and thumbnail_path.exists():
                try:
                    service.thumbnails().set(
                        videoId=video_id,
                        media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/png"),
                    ).execute()
                except Exception:
                    logger.warning("Failed to set thumbnail for video %s", video_id)

            playlist_id = self._get_or_create_playlist()
            if playlist_id:
                self._add_to_playlist(video_id, playlist_id)

            episode.youtube_video_id = video_id
            episode.youtube_url = youtube_url
            episode.status = EpisodeStatus.UPLOADED
            session.add(episode)
            session.flush()

            log_job_step(
                session, job_id, "youtube_upload", StepStatus.SUCCESS,
                f"Uploaded: {youtube_url} (playlist: {playlist_id or 'none'})",
            )
            return video_id
        except Exception as exc:
            log_job_step_isolated(job_id, "youtube_upload", StepStatus.FAILED, f"Upload failed: {exc}")
            logger.exception("YouTube upload failed for episode_id=%s", episode.id)
            raise

    def upload_short(
        self,
        session: Session,
        job_id: int,
        episode: Episode,
        short: Short,
        seo: EpisodeSEO | None = None,
    ) -> str:
        """Upload a Short to YouTube."""
        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            raise RuntimeError("google-api-python-client required for upload") from exc

        try:
            log_job_step(session, job_id, "youtube_short_upload", StepStatus.STARTED, "Uploading Short.")
            service = self._ensure_service()

            hashtags = seo.hashtags if seo else "#Spartacus #Gladiator"
            title = f"{episode.title} - Highlights {hashtags}"[:100]
            description = (
                "AI-generated gladiator animation. All characters are fictional.\n"
                f"Epic moment from Episode {episode.episode_number}! Full episode on our channel.\n{hashtags}"
            )

            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": (seo.tags if seo else [])[:15],
                    "categoryId": "24",
                },
                "status": {
                    "privacyStatus": self.privacy,
                    "selfDeclaredMadeForKids": False,
                },
            }

            media = MediaFileUpload(str(short.file_path), mimetype="video/mp4", resumable=True)
            request = service.videos().insert(part="snippet,status", body=body, media_body=media)

            response = None
            while response is None:
                _, response = request.next_chunk()

            video_id = response["id"]
            short.youtube_video_id = video_id
            short.youtube_url = f"https://www.youtube.com/shorts/{video_id}"
            session.add(short)
            session.flush()

            log_job_step(
                session, job_id, "youtube_short_upload", StepStatus.SUCCESS,
                f"Short uploaded: {short.youtube_url}",
            )
            return video_id
        except Exception as exc:
            log_job_step_isolated(job_id, "youtube_short_upload", StepStatus.FAILED, f"Short upload failed: {exc}")
            logger.exception("Short upload failed for episode_id=%s", episode.id)
            raise

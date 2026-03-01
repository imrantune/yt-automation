"""YouTube Data API v3 video uploader."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy.orm import Session

from config.settings import get_settings
from database.connection import log_job_step, log_job_step_isolated
from database.models import Episode, EpisodeSEO, EpisodeStatus, Short, StepStatus


logger = logging.getLogger(__name__)
settings = get_settings()


class YouTubeUploader:
    """Upload videos and shorts to YouTube via Data API v3."""

    def __init__(self) -> None:
        self.client_secret_path = os.getenv("YOUTUBE_CLIENT_SECRET_PATH", "")
        self.credentials_path = os.getenv("YOUTUBE_CREDENTIALS_PATH", "youtube_credentials.json")
        self.privacy = os.getenv("YOUTUBE_UPLOAD_PRIVACY", "unlisted")
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

        creds = None
        creds_file = Path(self.credentials_path)
        if creds_file.exists():
            creds = Credentials.from_authorized_user_file(str(creds_file))

        if not creds or not creds.valid:
            if not self.client_secret_path or not Path(self.client_secret_path).exists():
                raise RuntimeError(
                    f"YouTube client secret not found at '{self.client_secret_path}'. "
                    "Set YOUTUBE_CLIENT_SECRET_PATH in .env."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                self.client_secret_path,
                scopes=["https://www.googleapis.com/auth/youtube.upload"],
            )
            creds = flow.run_local_server(port=0)
            creds_file.write_text(creds.to_json())

        self._service = build("youtube", "v3", credentials=creds)
        return self._service

    def upload_video(
        self,
        session: Session,
        job_id: int,
        episode: Episode,
        video_path: Path,
        thumbnail_path: Path | None = None,
        seo: EpisodeSEO | None = None,
    ) -> str:
        """Upload video to YouTube and update episode record."""
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

            episode.youtube_video_id = video_id
            episode.youtube_url = youtube_url
            episode.status = EpisodeStatus.UPLOADED
            session.add(episode)
            session.flush()

            log_job_step(
                session, job_id, "youtube_upload", StepStatus.SUCCESS,
                f"Uploaded: {youtube_url}",
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

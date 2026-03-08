import logging
from database.connection import SessionLocal
from database.models import VideoJob
from sqlalchemy import desc, select

logging.basicConfig(level=logging.INFO)

session = SessionLocal()
try:
    job = session.execute(
        select(VideoJob).order_by(desc(VideoJob.id)).limit(1)
    ).scalar_one_or_none()
    print("Job status:", job.status.value if job else None)
except Exception as e:
    logging.exception("Error")
finally:
    session.close()

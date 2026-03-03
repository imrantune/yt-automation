"""Test FFmpeg merge utilities."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from pipeline.video_generator import _run_ffmpeg, _probe_duration


@patch("pipeline.video_generator.subprocess.run")
def test_run_ffmpeg_success(mock_run):
    """_run_ffmpeg should not raise on exit code 0."""
    mock_run.return_value = MagicMock(returncode=0, stderr="")
    _run_ffmpeg(["ffmpeg", "-version"], "test ffmpeg")
    mock_run.assert_called_once()


@patch("pipeline.video_generator.subprocess.run")
def test_run_ffmpeg_failure_raises(mock_run):
    """_run_ffmpeg should raise RuntimeError on non-zero exit."""
    mock_run.return_value = MagicMock(returncode=1, stderr="error: bad input")
    try:
        _run_ffmpeg(["ffmpeg", "-bad"], "test failure")
        assert False, "Should have raised"
    except RuntimeError as e:
        assert "bad input" in str(e)


@patch("pipeline.video_generator.subprocess.run")
def test_probe_duration(mock_run):
    """_probe_duration should parse ffprobe output."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="45.123\n",
        stderr="",
    )
    dur = _probe_duration(Path("/fake/clip.mp4"))
    assert abs(dur - 45.123) < 0.001


@patch("pipeline.video_generator.subprocess.run")
def test_probe_duration_handles_missing_file(mock_run):
    """_probe_duration should return 0.0 on failure."""
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout="",
        stderr="error",
    )
    dur = _probe_duration(Path("/nonexistent.mp4"))
    assert dur == 0.0

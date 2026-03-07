"""Pipeline run control: cancel request for graceful stop after current step."""

_cancel_requested = False


def request_cancel() -> None:
    """Request the running pipeline to stop after the current step."""
    global _cancel_requested
    _cancel_requested = True


def should_cancel() -> bool:
    """Return True if the pipeline should stop (user requested cancel)."""
    return _cancel_requested


def clear_cancel() -> None:
    """Clear cancel flag (e.g. when starting a new run)."""
    global _cancel_requested
    _cancel_requested = False

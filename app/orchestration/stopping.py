"""Stopping a running reply: what a stopped chat looks like in its checkpoint."""


def is_stopped(snapshot) -> bool:
    """A cut-off run has steps left (`next`) and is not waiting on a question (`interrupts`)."""
    return bool(getattr(snapshot, "next", ())) and not snapshot.interrupts

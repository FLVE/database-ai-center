import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

from dbic.core.redis_models import AuditSnapshot


def read_audit(directory: Path, now: datetime) -> AuditSnapshot:
    # Open each directory with NOFOLLOW so parent symlinks cannot escape the selected mount.
    parts = directory.absolute().parts
    descriptor = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[1:]:
            if part in {".", ".."}:
                raise ValueError("audit directory traversal forbidden")
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_fd = os.open(
            "redis-lab-01.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
        )
        with os.fdopen(file_fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("audit must be a regular file")
            raw = stream.read(65537)
    finally:
        os.close(descriptor)
    if len(raw) > 65536:
        raise ValueError("audit size limit exceeded")
    audit = AuditSnapshot.model_validate_json(raw)
    if audit.finished_at > now + timedelta(seconds=5):
        raise ValueError("audit time is in the future")
    if now - audit.finished_at > timedelta(minutes=15):
        raise LookupError("audit is stale")
    return audit

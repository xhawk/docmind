"""Where uploaded documents live.

PoC storage: a plain directory in the repo clone. See the storage TODO in
README.md — this module is the seam that changes when uploads move out of the
repo (per-user data dir, content-hash names, object store).
"""

import mimetypes
from pathlib import Path
from uuid import uuid4

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

# src/docmind/storage.py -> src/docmind -> src -> repo root. Anchoring on
# __file__ keeps the path the same wherever the server was launched from.
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "data" / "uploads"

# What the browser itself can render inline, which is a different question from
# what Claude will accept (see ai.ATTACHABLE_TYPES). The two agree today, but a
# .txt becomes previewable long before it becomes attachable.
PREVIEWABLE_TYPES = {
    "application/pdf": "document",
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/webp": "image",
}


def ensure_dir(upload_dir: Path) -> Path:
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def save(file_storage: FileStorage, upload_dir: Path) -> str:
    """Store an upload and return the name it was stored under.

    Raises ValueError if there's nothing usable to save.
    """
    # secure_filename strips directories, so "../../evil.pdf" becomes
    # "evil.pdf" -- but it can also reduce a name to "" (e.g. "..").
    name = secure_filename(file_storage.filename or "")
    if not name:
        raise ValueError("upload has no usable filename")

    # Readable tail so `ls` stays useful; uuid prefix so two scan.pdf uploads
    # don't clobber each other.
    stored_name = f"{uuid4().hex[:8]}-{name}"
    file_storage.save(upload_dir / stored_name)
    return stored_name


def list_files(upload_dir: Path) -> list[str]:
    """Names of stored uploads. The filesystem is the index for now."""
    if not upload_dir.is_dir():
        return []
    return sorted(p.name for p in upload_dir.iterdir() if p.is_file())


def resolve(name: str, upload_dir: Path) -> Path | None:
    """The path of a stored upload, or None if there is no such upload.

    Checked by membership in the real listing rather than by joining paths:
    downloads go through `send_from_directory`, which does its own traversal
    check, but here we open the file ourselves, so nothing else stops a
    `../../etc/passwd` from a URL.
    """
    if name not in list_files(upload_dir):
        return None
    return upload_dir / name


def read_bytes(name: str, upload_dir: Path) -> bytes:
    """The contents of a stored upload. Raises FileNotFoundError if unknown."""
    path = resolve(name, upload_dir)
    if path is None:
        raise FileNotFoundError(name)
    return path.read_bytes()


def media_type(name: str) -> str | None:
    """Best guess at the MIME type from the filename, or None."""
    return mimetypes.guess_type(name)[0]


def preview_kind(name: str) -> str | None:
    """How a browser can show this file inline: "document", "image", or None."""
    return PREVIEWABLE_TYPES.get(media_type(name) or "")

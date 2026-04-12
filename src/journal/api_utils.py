"""HTTP utility helpers for the REST API layer.

Provides multipart form-data parsing and shared request-reading
helpers used by the ingestion endpoints. These sit between raw
ASGI and the application layer so that endpoint handlers stay
focused on business logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request

log = logging.getLogger(__name__)


@dataclass
class UploadedFile:
    """A single file extracted from a multipart upload."""

    filename: str
    content_type: str
    data: bytes


async def parse_multipart_request(
    request: Request,
) -> tuple[dict[str, str], dict[str, list[UploadedFile]]]:
    """Parse a multipart/form-data request into fields and files.

    Returns ``(fields, files)`` where *fields* maps field names to
    their string values and *files* maps field names to lists of
    `UploadedFile` objects. Multiple files under the same field name
    (e.g. ``images``) accumulate in the list in submission order.

    Uses Starlette's built-in form parsing which delegates to
    ``python-multipart`` under the hood.
    """
    fields: dict[str, str] = {}
    files: dict[str, list[UploadedFile]] = {}

    form = await request.form()
    for key, value in form.multi_items():
        if hasattr(value, "read"):
            # It's an UploadFile
            data = await value.read()
            uploaded = UploadedFile(
                filename=value.filename or "",
                content_type=value.content_type or "application/octet-stream",
                data=data,
            )
            files.setdefault(key, []).append(uploaded)
        else:
            # It's a plain string field
            fields[key] = str(value)

    await form.close()
    return fields, files

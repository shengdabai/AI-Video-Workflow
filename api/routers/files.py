# Copyright (C) 2025 AIDC-AI
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
File service endpoints

Provides access to generated files (videos, images, audio) and resource files.
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from loguru import logger

from api.pathsafe import resolve_file_inside, split_relative_path

router = APIRouter(prefix="/files", tags=["Files"])

# Directories (relative to the working directory) that this endpoint serves.
# A request path that starts with one of them is looked up in that directory;
# any other request path is looked up in output/ (backward compatibility).
_SERVED_ROOTS = (
    "output",
    "workflows",
    "templates",
    "bgm",
    "data/bgm",
    "data/templates",
    "resources",
)
_DEFAULT_ROOT = "output"


def _resolve_served_file(file_path: str) -> Optional[Path]:
    """
    Map a request path to a regular file inside one of the served roots.

    The ASGI server has already percent-decoded the path once; it is not
    decoded again. Returns None when the path is empty, contains a NUL byte,
    is absolute, contains a ".." segment, or resolves (after following
    symlinks) to anything that is not a regular file inside its root.
    """
    parts = split_relative_path(file_path)
    if parts is None:
        return None

    root_rel = _DEFAULT_ROOT
    rel_parts = parts
    for candidate in _SERVED_ROOTS:
        candidate_parts = tuple(candidate.split("/"))
        if len(parts) > len(candidate_parts) and parts[: len(candidate_parts)] == candidate_parts:
            root_rel = candidate
            rel_parts = parts[len(candidate_parts):]
            break

    # root_rel is relative, so it is resolved against the working directory.
    return resolve_file_inside(root_rel, *rel_parts)


@router.get("/{file_path:path}")
async def get_file(file_path: str):
    """
    Get file by path
    
    Serves files from allowed directories:
    - output/ - Generated files (videos, images, audio)
    - workflows/ - ComfyUI workflow files
    - templates/ - HTML templates
    - bgm/ - Background music
    - data/bgm/ - Custom background music
    - data/templates/ - Custom templates
    - resources/ - Other resources (images, fonts, etc.)
    
    - **file_path**: File path relative to allowed directories
    
    Examples:
    - "abc123.mp4" → output/abc123.mp4
    - "workflows/runninghub/image_flux.json" → workflows/runninghub/image_flux.json
    - "templates/1080x1920/default.html" → templates/1080x1920/default.html
    - "bgm/default.mp3" → bgm/default.mp3
    - "resources/example.png" → resources/example.png
    
    Returns file for download or preview.
    """
    try:
        abs_path = _resolve_served_file(file_path)
        if abs_path is None:
            # Same answer for missing files and for paths outside the served
            # roots, so the response does not reveal what exists elsewhere.
            raise HTTPException(status_code=404, detail="File not found")

        # Determine media type
        suffix = abs_path.suffix.lower()
        media_types = {
            '.mp4': 'video/mp4',
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.html': 'text/html',
            '.json': 'application/json',
        }
        media_type = media_types.get(suffix, 'application/octet-stream')
        
        # Use inline disposition for browser preview
        return FileResponse(
            path=str(abs_path),
            media_type=media_type,
            filename=abs_path.name,
            content_disposition_type="inline",
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File access error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


"""Bounded audio uploads. Client filenames never become server filesystem paths."""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from beatforge.connector_store import ConnectorStore


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filename: str = Field(min_length=1, max_length=200)
    size: int = Field(ge=1, le=64 * 1024 * 1024, strict=True)

    @field_validator('filename')
    @classmethod
    def audio_filename(cls, value: str) -> str:
        if any(ord(char) < 32 or char in '/\\:' for char in value):
            raise ValueError('Supply a filename, not a path')
        if Path(value).suffix.lower() not in {'.wav', '.ogg', '.mp3', '.flac', '.m4a', '.mp4'}:
            raise ValueError('Unsupported audio extension')
        return value


class Uploads:
    def __init__(self, store: ConnectorStore, root: Path):
        self.store, self.root = store, Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, upload_id: str) -> Path:
        # Only validated server identifiers can address a blob.
        if len(upload_id) != 32 or any(char not in '0123456789abcdef' for char in upload_id):
            raise KeyError('Upload not found')
        path = (self.root / (upload_id + '.audio')).resolve()
        if path.parent != self.root:
            raise KeyError('Upload not found')
        return path

    async def receive(self, owner: str, upload_id: str, request: Request) -> dict:
        metadata = self.store.get_upload(owner, upload_id)
        path = self.path(upload_id)
        size = request.headers.get('content-length')
        if size is not None and (not size.isdecimal() or int(size) != metadata['size']):
            raise HTTPException(413, 'Upload size differs from its reservation')
        self.store.begin_upload(owner, upload_id)
        temporary = path.with_suffix('.part')
        try:
            digest, received = hashlib.sha256(), 0
            # Exclusive creation also refuses stale files after an interrupted process.
            with temporary.open('xb') as stream:
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > metadata['size']:
                        raise HTTPException(413, 'Upload exceeds its reserved size')
                    stream.write(chunk)
                    digest.update(chunk)
            if received != metadata['size']:
                raise HTTPException(422, 'Upload is incomplete')
            temporary.replace(path)
            self.store.complete_upload(owner, upload_id, digest.hexdigest())
        except BaseException:
            # The immutable ready blob is never touched by a second upload request.
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            self.store.abort_upload(owner, upload_id)
            raise
        return self.store.get_upload(owner, upload_id)

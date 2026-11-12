"""Where uploaded photos live.

STORAGE_BACKEND=local  a folder (UPLOAD_FOLDER), served by the /media route. Fine for development,
                       or on a host with a persistent disk. Most free hosts WIPE local disk on deploy.
STORAGE_BACKEND=s3     any S3-compatible bucket (Cloudflare R2, AWS S3, Backblaze B2...), served
                       straight from S3_PUBLIC_BASE_URL so photos never pass through the web server.

Callers only ever deal in `storage_key` strings, so switching backend changes nothing else.
Existing local photos are NOT moved automatically: copy the uploads/ folder into the bucket
(same file names) when you switch.
"""
import logging
import os
import uuid

from flask import current_app, url_for

from services.images import process_upload, process_avatar, process_banner

log = logging.getLogger(__name__)
CACHE_CONTROL = "public, max-age=31536000, immutable"  # keys are random and never reused


def _use_s3() -> bool:
    return current_app.config["STORAGE_BACKEND"] == "s3"


def thumb_key(key: str) -> str:
    root, ext = os.path.splitext(key)
    return f"{root}_t{ext}"


# --- S3 ---------------------------------------------------------------------------
def _s3_client():
    client = current_app.extensions.get("s3_client")
    if client is None:
        import boto3  # imported lazily: local development doesn't need it installed

        cfg = current_app.config
        client = boto3.client(
            "s3",
            endpoint_url=cfg["S3_ENDPOINT_URL"] or None,
            region_name=cfg["S3_REGION"] or None,
            aws_access_key_id=cfg["S3_ACCESS_KEY_ID"],
            aws_secret_access_key=cfg["S3_SECRET_ACCESS_KEY"],
        )
        current_app.extensions["s3_client"] = client
    return client


def _s3_delete(keys) -> None:
    for key in keys:
        try:
            _s3_client().delete_object(Bucket=current_app.config["S3_BUCKET"], Key=key)
        except Exception:  # an orphaned object is better than failing the user's action
            log.warning("Could not delete %s from the bucket", key, exc_info=True)


# --- local ------------------------------------------------------------------------
def _folder() -> str:
    return current_app.config["UPLOAD_FOLDER"]


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


# --- public API ---------------------------------------------------------------------
def save_image(file_storage) -> str:
    """Process and store an upload; return its storage key. Raises ImageError."""
    full, thumb = process_upload(file_storage)
    key = f"{uuid.uuid4().hex}.webp"
    objects = [(key, full), (thumb_key(key), thumb)]

    if _use_s3():
        stored = []
        try:
            for name, data in objects:
                _s3_client().put_object(
                    Bucket=current_app.config["S3_BUCKET"], Key=name, Body=data,
                    ContentType="image/webp", CacheControl=CACHE_CONTROL,
                )
                stored.append(name)
        except Exception:
            _s3_delete(stored)  # no half-uploaded photos
            raise
        return key

    paths = [os.path.join(_folder(), name) for name, _ in objects]
    try:
        for path, (_, data) in zip(paths, objects):
            with open(path, "wb") as fh:
                fh.write(data)
    except Exception:
        for path in paths:
            _remove(path)
        raise
    return key



def save_avatar(file_storage) -> str:
    full, thumb = process_avatar(file_storage)
    key = f"{uuid.uuid4().hex}.webp"
    objects = [(key, full), (thumb_key(key), thumb)]
    if _use_s3():
        stored = []
        try:
            for name, data in objects:
                _s3_client().put_object(Bucket=current_app.config["S3_BUCKET"], Key=name, Body=data, ContentType="image/webp", CacheControl=CACHE_CONTROL)
                stored.append(name)
        except Exception:
            _s3_delete(stored); raise
    else:
        paths = [os.path.join(_folder(), name) for name, _ in objects]
        try:
            for path, (_, data) in zip(paths, objects):
                with open(path, "wb") as fh: fh.write(data)
        except Exception:
            for path in paths: _remove(path)
            raise
    return key


def save_banner(file_storage) -> str:
    data = process_banner(file_storage)
    key = f"{uuid.uuid4().hex}.webp"
    if _use_s3():
        _s3_client().put_object(Bucket=current_app.config["S3_BUCKET"], Key=key, Body=data,
                                ContentType="image/webp", CacheControl=CACHE_CONTROL)
    else:
        with open(os.path.join(_folder(), key), "wb") as fh:
            fh.write(data)
    return key


def delete_avatar_files(keys):
    delete_image_files(keys)


def avatar_url(key):
    return _public_url(key)


def avatar_thumb_url(key):
    return _public_url(thumb_key(key))

def delete_image_files(keys) -> None:
    names = [name for key in keys for name in (key, thumb_key(key))]
    if _use_s3():
        _s3_delete(names)
    else:
        for name in names:
            _remove(os.path.join(_folder(), name))


def _public_url(key: str) -> str:
    if _use_s3():
        return f"{current_app.config['S3_PUBLIC_BASE_URL'].rstrip('/')}/{key}"
    return url_for("media", filename=key)


def image_url(key: str) -> str:
    return _public_url(key)


def thumb_url(key: str) -> str:
    return _public_url(thumb_key(key))

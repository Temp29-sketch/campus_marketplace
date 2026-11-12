"""Validate and process uploaded photos.

We trust the file's real contents, not its name or content-type header:
the image is fully decoded with Pillow, so renamed or corrupt files are refused.
Phone photos are auto-rotated, stripped of metadata (incl. GPS), shrunk, and
re-encoded as WebP, which keeps the feed fast on campus mobile data.
"""
import io
import warnings

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


class ImageError(ValueError):
    """A user-facing problem with an uploaded image."""


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.getchannel("A"))
        return background
    return img.convert("RGB")


def _encode(img: Image.Image, max_side: int) -> bytes:
    copy = img.copy()
    copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)  # never upscales
    buffer = io.BytesIO()
    copy.save(buffer, "WEBP", quality=82, method=4)
    return buffer.getvalue()


def process_upload(file_storage):
    """Return (full_size_bytes, thumbnail_bytes) as WebP, or raise ImageError."""
    cfg = current_app.config
    name = file_storage.filename or "file"
    max_bytes = cfg["MAX_IMAGE_BYTES"]

    data = file_storage.read(max_bytes + 1)
    if not data:
        raise ImageError(f"{name} is empty.")
    if len(data) > max_bytes:
        raise ImageError(f"{name} is too large (max {max_bytes // (1024 * 1024)} MB per photo).")

    Image.MAX_IMAGE_PIXELS = cfg["MAX_IMAGE_PIXELS"]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            if img.format not in ALLOWED_FORMATS:
                raise ImageError(f"{name}: only JPG, PNG or WebP photos are allowed.")
            img.load()  # full decode: catches truncated/corrupt files
    except ImageError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError):
        raise ImageError(f"{name} isn't a valid image.")

    img = _to_rgb(ImageOps.exif_transpose(img))
    return _encode(img, cfg["IMAGE_MAX_SIDE"]), _encode(img, cfg["THUMB_MAX_SIDE"])


def process_avatar(file_storage):
    """Return square 512px and 128px WebP avatar bytes with the upload checks applied."""
    full, _ = process_upload(file_storage)
    with Image.open(io.BytesIO(full)) as img:
        square = ImageOps.fit(img.convert("RGB"), (512, 512), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        thumb = square.resize((128, 128), Image.Resampling.LANCZOS)
        def encode(image):
            out = io.BytesIO(); image.save(out, "WEBP", quality=82, method=4); return out.getvalue()
        return encode(square), encode(thumb)


def process_banner(file_storage):
    """Validate and resize banner artwork without cropping; display CSS crops it."""
    cfg = current_app.config
    name = file_storage.filename or "banner"
    data = file_storage.read(cfg["MAX_IMAGE_BYTES"] + 1)
    if not data or len(data) > cfg["MAX_IMAGE_BYTES"]:
        raise ImageError(f"{name} is empty or too large.")
    Image.MAX_IMAGE_PIXELS = cfg["MAX_IMAGE_PIXELS"]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            if img.format not in ALLOWED_FORMATS:
                raise ImageError(f"{name}: only JPG, PNG or WebP photos are allowed.")
            img.load()
    except ImageError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError):
        raise ImageError(f"{name} isn't a valid image.")
    img = _to_rgb(ImageOps.exif_transpose(img))
    if img.width > 1600:
        resized_height = max(1, round(img.height * 1600 / img.width))
        img = img.resize((1600, resized_height), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "WEBP", quality=82, method=4)
    return out.getvalue()

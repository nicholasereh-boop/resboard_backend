"""Upload hardening: decode with Pillow, cross-check name and type, strip ALL metadata, safe filenames.

The file's own bytes decide what it is. The client's filename and Content-Type are only
cross-checked and must agree with the detected format (a PNG renamed to .jpg is rejected).
Every accepted image is DECODED and RE-ENCODED from its pixels, so nothing else in the upload survives: no EXIF or GPS,
no ICC profile, no GIF comments, no text chunks, no bytes appended after the image (the classic "polyglot" trick),
no script hidden in a metadata field. Animated GIF / WebP / PNG are re-encoded frame by frame (limits below).
"""
import os
import re
import unicodedata
import uuid
import warnings
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, ImageSequence
from rest_framework import serializers

MAX_PIXELS = 40_000_000  # decompression-bomb guard
MAX_SIDE = 12000
MAX_FRAMES = 200  # animation guard: frame count ...
MAX_TOTAL_PIXELS = 150_000_000  # ... and width x height x frames (a few small frames are fine, a thousand big ones are not)
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

# detected format -> (extension we store, accepted extensions, accepted Content-Type)
FORMATS = {
    "JPEG": (".jpg", {".jpg", ".jpeg"}, "image/jpeg"),
    "PNG": (".png", {".png"}, "image/png"),
    "WEBP": (".webp", {".webp"}, "image/webp"),
    "GIF": (".gif", {".gif"}, "image/gif"),
}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ONLY_THESE = "only JPEG, PNG, WebP and GIF images are allowed."


class UploadRejected(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _label(upload):
    """A short, safe name for error messages (never used for storage)."""
    name = os.path.basename(str(getattr(upload, "name", "file")).replace("\\", "/"))
    return name[:60] or "file"


def display_name_of(upload):
    """The client's file name, kept ONLY as text to show in the admin: no path, no control or markup characters."""
    name = os.path.basename(str(getattr(upload, "name", "") or "").replace("\\", "/"))
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cf") and ch not in "<>\"'`")
    return re.sub(r"\s+", " ", name).strip()[:120]


def _reencode_animated(image, detected):
    """Rebuild an animation from its decoded frames. Raises UploadRejected when it is too big."""
    frames, durations = [], []
    width, height = image.size
    for frame in ImageSequence.Iterator(image):
        if len(frames) >= MAX_FRAMES or width * height * (len(frames) + 1) > MAX_TOTAL_PIXELS:
            raise UploadRejected(f"animation is too long or too large (at most {MAX_FRAMES} frames).")
        frames.append(frame.convert("RGBA"))  # a fresh image: nothing from the original's info dict comes along
        durations.append(frame.info.get("duration", 100))
    loop = image.info.get("loop", 0)
    out = BytesIO()
    if detected == "GIF":
        frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:], duration=durations, loop=loop, disposal=2)
    elif detected == "WEBP":
        frames[0].save(out, format="WEBP", save_all=True, append_images=frames[1:], duration=durations, loop=loop, quality=88)
    else:
        frames[0].save(out, format="PNG", save_all=True, append_images=frames[1:], duration=durations, loop=loop)
    return out.getvalue()


def _limit_text(limit):
    return f"{limit / (1024 * 1024):g} MB" if limit >= 1024 * 1024 else f"{max(1, limit // 1024)} KB"


def _check(upload):
    """Returns (ContentFile, metadata) or raises UploadRejected(message)."""
    if upload.size > settings.MEDIA_MAX_UPLOAD_BYTES:
        raise UploadRejected(f"file is larger than {_limit_text(settings.MEDIA_MAX_UPLOAD_BYTES)}.")

    extension = os.path.splitext(os.path.basename(str(upload.name).replace("\\", "/")))[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise UploadRejected(ONLY_THESE)
    declared = (getattr(upload, "content_type", "") or "").split(";")[0].strip().lower()
    if declared not in ALLOWED_CONTENT_TYPES:
        raise UploadRejected(ONLY_THESE)

    # Decode. Pillow's decompression-bomb warning is turned into an error so we can refuse the file.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(upload)
            probe.verify()
        upload.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(upload)
            detected = (image.format or "").upper()
            if detected == "MPO":  # multi-picture JPEG written by many phones
                detected = "JPEG"
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise UploadRejected(f"image is too large ({width}x{height} px).")
            if max(width, height) > MAX_SIDE:
                raise UploadRejected(f"image is too large (longest side is limited to {MAX_SIDE} px).")
            image.load()
    except UploadRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise UploadRejected("image has too many pixels.")
    except Exception:
        raise UploadRejected("this is not a valid image file.")

    if detected not in FORMATS:
        raise UploadRejected(ONLY_THESE)
    stored_ext, accepted_exts, mime = FORMATS[detected]
    if extension not in accepted_exts or declared != mime:
        raise UploadRejected(f"the file name or type does not match its content (it is a {detected} image).")

    animated = bool(getattr(image, "is_animated", False)) and getattr(image, "n_frames", 1) > 1
    if animated:
        data = _reencode_animated(image, detected)
    elif detected == "GIF":
        out = BytesIO()
        image.convert("RGBA").save(out, format="GIF")  # a single frame, rebuilt from pixels (comments and trailing bytes gone)
        data = out.getvalue()
    else:
        image = ImageOps.exif_transpose(image)  # bake the rotation in...
        if detected == "JPEG" and image.mode not in ("RGB", "L"):
            image = image.convert("RGB")  # e.g. CMYK
        out = BytesIO()
        # ...then re-encode WITHOUT passing any exif / xmp / icc_profile, which drops EXIF, GPS, XMP and colour-profile text.
        if detected == "JPEG":
            image.save(out, format="JPEG", quality=88, optimize=True)
        elif detected == "WEBP":
            image.save(out, format="WEBP", quality=88)
        else:
            image.save(out, format="PNG", optimize=True)
        data = out.getvalue()
        width, height = image.size

    # The stored name is random; nothing from the client reaches the path.
    content = ContentFile(data, name=f"{uuid.uuid4().hex}{stored_ext}")
    meta = {
        "file_type": "image", "mime_type": mime, "width": width, "height": height, "size_bytes": len(data),
        "original_name": display_name_of(upload),  # display text only; the stored file name above is random
    }
    return content, meta


def process_upload(upload):
    """Validate ONE upload. Raises a DRF ValidationError such as {"file": ["photo.jpg: file is larger than 5 MB."]}."""
    try:
        return _check(upload)
    except UploadRejected as exc:
        raise serializers.ValidationError({"file": [f"{_label(upload)}: {exc.message}"]})


def process_uploads(uploads):
    """Validate ALL uploads before anything is stored; report every problem file in one response."""
    processed, problems = [], []
    for upload in uploads:
        try:
            processed.append(_check(upload))
        except UploadRejected as exc:
            problems.append(f"{_label(upload)}: {exc.message}")
    if problems:
        raise serializers.ValidationError({"file": problems})
    return processed

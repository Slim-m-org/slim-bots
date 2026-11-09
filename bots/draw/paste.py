"""Pasting a picture as one canvas image object: shrink it for upload and fit it into a free region; Pillow only."""

import io

import pictures
from plan import Planned
from sources import SourceError

UPLOAD_SIDE = 1600  # pixels on the long side an uploaded picture is limited to
QUALITY = 88


def prepare(data):
    """(JPEG bytes, width, height) of the picture shrunk to at most UPLOAD_SIDE pixels."""
    try:
        with pictures.open_picture(data) as picture:
            picture.draft("RGB", (UPLOAD_SIDE * 2, UPLOAD_SIDE * 2))
            rgb = picture.convert("RGB")
            rgb.thumbnail((UPLOAD_SIDE, UPLOAD_SIDE))
            out = io.BytesIO()
            rgb.save(out, "JPEG", quality=QUALITY)
    except Exception as err:
        raise SourceError("the picture could not be read") from err
    return out.getvalue(), rgb.width, rgb.height


def image_object(attachment_id, width, height, region):
    """The canvas `image` object for an uploaded picture, as large as fits `region` with its aspect ratio, centred."""
    scale = min(region.w / width, region.h / height)
    w, h = width * scale, height * scale
    return Planned("image", region.x + (region.w - w) / 2, region.y + (region.h - h) / 2, w, h, {"attachment": attachment_id})

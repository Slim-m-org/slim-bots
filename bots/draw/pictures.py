"""The one place the draw bot opens a picture, so the pixel bound holds whichever code path ran first."""

import io

MAX_PIXELS = 25_000_000

try:
    from PIL import Image
except ImportError:  # the bot still starts; a drawing request says Pillow is missing
    Image = None
else:
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class PillowMissing(Exception):
    """Pillow is not installed; `str(self)` is safe to show."""


class PictureTooLarge(ValueError):
    """A picture whose header declares more than MAX_PIXELS."""


def open_picture(data):
    """A lazily decoded Pillow image of `data`; one declaring more than MAX_PIXELS is refused from its header alone."""
    if Image is None:
        raise PillowMissing("Pillow is not installed on this bot, so it cannot read pictures")
    picture = Image.open(io.BytesIO(data))
    width, height = picture.size
    if width * height > MAX_PIXELS:
        picture.close()
        raise PictureTooLarge(f"{width}x{height} is past {MAX_PIXELS} pixels")
    return picture

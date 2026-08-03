"""Photo normalization for generated desktop pet packages.

The pipeline intentionally has a lightweight fallback so the MVP works with
plain Pillow. Installing ``rembg`` enables model-based background removal.
"""

from collections import deque
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple


def _remove_simple_background(image, tolerance: int = 28):
    """Remove a connected border background close to the top-left pixel.

    This is only a fallback for simple, mostly solid backgrounds. It leaves
    photos with an existing alpha channel untouched apart from transparent
    pixels already present.
    """

    from PIL import Image

    image = image.convert("RGBA")
    width, height = image.size
    if width < 2 or height < 2:
        return image

    alpha = image.getchannel("A")
    if alpha.getextrema() != (255, 255):
        return image

    pixels = image.load()
    background = pixels[0, 0][:3]
    limit = tolerance * tolerance
    visited = bytearray(width * height)
    seeds = []
    for x in range(width):
        seeds.append((x, 0))
        seeds.append((x, height - 1))
    for y in range(1, height - 1):
        seeds.append((0, y))
        seeds.append((width - 1, y))
    queue = deque(seeds)

    while queue:
        x, y = queue.popleft()
        position = y * width + x
        if visited[position]:
            continue
        visited[position] = 1

        red, green, blue, _ = pixels[x, y]
        distance = (
            (red - background[0]) ** 2
            + (green - background[1]) ** 2
            + (blue - background[2]) ** 2
        )
        if distance > limit:
            continue

        pixels[x, y] = (red, green, blue, 0)
        if x > 0:
            queue.append((x - 1, y))
        if x + 1 < width:
            queue.append((x + 1, y))
        if y > 0:
            queue.append((x, y - 1))
        if y + 1 < height:
            queue.append((x, y + 1))

    return image


def _remove_background(image, use_rembg: bool = False, background_mode: Optional[str] = None):
    from PIL import Image

    mode = str(background_mode or ("rembg" if use_rembg else "simple")).strip().lower()
    if mode == "none":
        return image.convert("RGBA")

    if mode in {"auto", "rembg"} or use_rembg:
        try:
            from rembg import remove  # type: ignore

            source = BytesIO()
            image.save(source, format="PNG")
            result = remove(source.getvalue())
            return Image.open(BytesIO(result)).convert("RGBA")
        except Exception:
            # The optional model is deliberately best-effort. The generated
            # package is still useful with the simple fallback below.
            pass

    return _remove_simple_background(image)


def subject_bbox(image) -> Optional[Tuple[int, int, int, int]]:
    """Return the visible subject bounds for an RGBA image."""

    return image.convert("RGBA").getchannel("A").getbbox()


def _paste_subject(canvas, image, padding: int, anchor: str):
    left = (canvas.width - image.width) // 2
    normalized_anchor = str(anchor or "center").strip().lower()
    if normalized_anchor in {"bottom", "baseline", "bottom-center"}:
        top = canvas.height - padding - image.height
    elif normalized_anchor == "top":
        top = padding
    else:
        top = (canvas.height - image.height) // 2
    canvas.alpha_composite(image, (left, top))


def normalize_pet_image(
    source_path: Path,
    destination_path: Path,
    canvas_size: int = 320,
    use_rembg: bool = False,
    background_mode: Optional[str] = None,
    anchor: str = "center",
    subject_scale: float = 1.0,
) -> None:
    """Normalize one user photo to a transparent, pose-aligned square PNG.

    ``anchor="bottom"`` keeps the subject's baseline stable across walking
    frames.  A center anchor remains the default for backwards compatibility
    and works better for curled sleeping poses.  ``background_mode`` accepts
    ``none``, ``simple``, ``rembg`` or ``auto``; the latter two gracefully
    fall back to the lightweight border flood-fill when rembg is unavailable.
    """

    from PIL import Image, ImageOps

    with Image.open(source_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")

    image = _remove_background(
        image,
        use_rembg=use_rembg,
        background_mode=background_mode,
    )
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox:
        image = image.crop(bbox)

    if image.width == 0 or image.height == 0:
        raise ValueError(f"无法从照片中读取有效图像: {source_path}")

    padding = max(4, int(canvas_size * 0.08))
    try:
        scale = float(subject_scale)
    except (TypeError, ValueError):
        scale = 1.0
    scale = max(0.5, min(1.0, scale))
    target_size = max(1, round((canvas_size - padding * 2) * scale))
    image.thumbnail((target_size, target_size), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    _paste_subject(canvas, image, padding, anchor)

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination_path, format="PNG", optimize=True)

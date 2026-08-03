"""Photo normalization for generated desktop pet packages.

The pipeline intentionally has a lightweight fallback so the MVP works with
plain Pillow. Installing ``rembg`` enables model-based background removal.
"""

from collections import deque
from io import BytesIO
from pathlib import Path


def _remove_simple_background(image, tolerance: int = 28):
    """Remove a connected background close to the top-left pixel.

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
    queue = deque([(0, 0)])

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


def _remove_background(image, use_rembg: bool):
    from PIL import Image

    if use_rembg:
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


def normalize_pet_image(
    source_path: Path,
    destination_path: Path,
    canvas_size: int = 320,
    use_rembg: bool = False,
) -> None:
    """Normalize one user photo to a transparent square PNG."""

    from PIL import Image, ImageOps

    with Image.open(source_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")

    image = _remove_background(image, use_rembg=use_rembg)
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox:
        image = image.crop(bbox)

    if image.width == 0 or image.height == 0:
        raise ValueError(f"无法从照片中读取有效图像: {source_path}")

    padding = max(4, int(canvas_size * 0.08))
    target_size = max(1, canvas_size - padding * 2)
    image.thumbnail((target_size, target_size), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    left = (canvas_size - image.width) // 2
    top = (canvas_size - image.height) // 2
    canvas.alpha_composite(image, (left, top))

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination_path, format="PNG", optimize=True)

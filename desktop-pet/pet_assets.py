"""Photo normalization for generated desktop pet packages.

The pipeline intentionally has a lightweight fallback so the MVP works with
plain Pillow. The server asks the image model for transparent PNG output and
only uses Pillow here for alpha cleanup, cropping, and frame alignment.
"""

from collections import deque
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


def _clean_alpha_edges(image, minimum_alpha: int = 8):
    """Remove almost-transparent matte noise and clear RGB fringe pixels.

    Segmentation models generally return a good alpha matte, but RGB values
    from the old background can remain in fully transparent pixels. Those
    values become a visible white/green halo after repeated compositing. Keep
    useful semi-transparent fur while dropping only the very weakest noise.
    """

    from PIL import Image

    image = image.convert("RGBA")
    alpha = image.getchannel("A").point(
        lambda value: 0 if value < minimum_alpha else value
    )
    image.putalpha(alpha)
    # Use Pillow's native compositing instead of a Python pixel loop; AI jobs
    # may contain dozens of 1024px frames and this path must stay affordable.
    transparent = Image.new("RGBA", image.size, (0, 0, 0, 0))
    nonzero = alpha.point(lambda value: 255 if value else 0)
    return Image.composite(image, transparent, nonzero)


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


def _remove_background(
    image,
    background_mode: Optional[str] = None,
):
    mode = str(background_mode or "simple").strip().lower()
    if mode == "none":
        return _clean_alpha_edges(image)

    return _clean_alpha_edges(_remove_simple_background(image))


def subject_bbox(image) -> Optional[Tuple[int, int, int, int]]:
    """Return the visible subject bounds for an RGBA image."""

    return image.convert("RGBA").getchannel("A").getbbox()


def _contact_sheet_grids(frame_count: int) -> List[Tuple[int, int]]:
    """Return likely contact-sheet layouts for a requested frame count."""

    return {
        2: [(2, 1), (1, 2)],
        3: [(3, 1), (1, 3)],
        4: [(2, 2), (4, 1), (1, 4)],
        5: [(5, 1), (1, 5)],
        6: [(3, 2), (2, 3), (6, 1), (1, 6)],
        8: [(4, 2), (2, 4), (8, 1), (1, 8)],
        9: [(3, 3)],
        10: [(5, 2), (2, 5)],
        12: [(4, 3), (3, 4)],
    }.get(frame_count, [])


def _contact_sheet_grid(frame_count: int) -> Optional[Tuple[int, int]]:
    """Return the preferred contact-sheet grid for compatibility."""

    grids = _contact_sheet_grids(frame_count)
    return grids[0] if grids else None


def _contact_sheet_tile_bounds(image, columns: int, rows: int, column: int, row: int):
    width, height = image.size
    return (
        round(width * column / columns),
        round(height * row / rows),
        round(width * (column + 1) / columns),
        round(height * (row + 1) / rows),
    )


def _contact_sheet_seam_score(image, columns: int, rows: int) -> float:
    """Estimate how discontinuous the regular grid seams are."""

    width, height = image.size
    pixels = image.convert("RGBA").load()
    differences = []
    for column in range(1, columns):
        x = round(width * column / columns)
        for y in range(height):
            left = pixels[max(0, x - 1), y]
            right = pixels[min(width - 1, x), y]
            differences.append(sum(abs(left[index] - right[index]) for index in range(4)) / 1020.0)
    for row in range(1, rows):
        y = round(height * row / rows)
        for x in range(width):
            top = pixels[x, max(0, y - 1)]
            bottom = pixels[x, min(height - 1, y)]
            differences.append(sum(abs(top[index] - bottom[index]) for index in range(4)) / 1020.0)
    return sum(differences) / len(differences) if differences else 0.0


def _contact_sheet_tile_has_content(tile) -> bool:
    """Return whether one candidate cell contains a visible subject."""

    from PIL import ImageStat

    tile = tile.convert("RGBA")
    alpha = tile.getchannel("A")
    alpha_min, alpha_max = alpha.getextrema()
    if alpha_min < 255:
        alpha_bbox = alpha.getbbox()
        if alpha_bbox:
            bbox_area = (alpha_bbox[2] - alpha_bbox[0]) * (alpha_bbox[3] - alpha_bbox[1])
            if bbox_area >= tile.width * tile.height * 0.01:
                return True

    rgb = tile.convert("RGB")
    corners = [
        rgb.getpixel((0, 0)),
        rgb.getpixel((max(0, rgb.width - 1), 0)),
        rgb.getpixel((0, max(0, rgb.height - 1))),
        rgb.getpixel((max(0, rgb.width - 1), max(0, rgb.height - 1))),
    ]
    background = tuple(sum(pixel[index] for pixel in corners) / len(corners) for index in range(3))
    step = max(1, min(rgb.width, rgb.height) // 64)
    foreground = 0
    samples = 0
    for y in range(0, rgb.height, step):
        for x in range(0, rgb.width, step):
            pixel = rgb.getpixel((x, y))
            distance = sum((pixel[index] - background[index]) ** 2 for index in range(3))
            if distance >= 24 * 24 * 3:
                foreground += 1
            samples += 1
    foreground_ratio = foreground / float(max(1, samples))
    variance = sum(ImageStat.Stat(rgb).var) / 3.0
    return foreground_ratio >= 0.01 or variance >= 90.0


def _contact_sheet_content_count(image, columns: int, rows: int) -> int:
    """Count grid cells that contain a visible or visually complex subject."""

    visible = 0
    for row in range(rows):
        for column in range(columns):
            tile = image.crop(_contact_sheet_tile_bounds(image, columns, rows, column, row))
            if _contact_sheet_tile_has_content(tile):
                visible += 1
    return visible


def _contact_sheet_background_score(image, columns: int, rows: int) -> float:
    """Estimate whether cells share a repeated flat/transparent background."""

    colors = []
    for row in range(rows):
        for column in range(columns):
            tile = image.crop(_contact_sheet_tile_bounds(image, columns, rows, column, row)).convert("RGBA")
            colors.extend(
                [
                    tile.getpixel((0, 0)),
                    tile.getpixel((max(0, tile.width - 1), 0)),
                    tile.getpixel((0, max(0, tile.height - 1))),
                    tile.getpixel((max(0, tile.width - 1), max(0, tile.height - 1))),
                ]
            )
    if not colors:
        return 0.0
    average = tuple(sum(color[index] for color in colors) / len(colors) for index in range(4))
    deviation = sum(
        sum(abs(color[index] - average[index]) for index in range(4)) / 1020.0
        for color in colors
    ) / len(colors)
    return max(0.0, min(1.0, 1.0 - deviation / 0.30))


def _contact_sheet_score(image, columns: int, rows: int, frame_count: int) -> float:
    """Score a candidate grid while avoiding ordinary single-frame images."""

    content_count = _contact_sheet_content_count(image, columns, rows)
    minimum_content = max(2, (frame_count * 3 + 4) // 5)
    if content_count < minimum_content:
        return -1.0

    seam_score = _contact_sheet_seam_score(image, columns, rows)
    background_score = _contact_sheet_background_score(image, columns, rows)
    content_ratio = content_count / float(frame_count)

    # A collage usually has either hard cell seams or the same flat/transparent
    # background repeated around each independently framed subject. A normal
    # single image generally has neither signal across most cells.
    has_grid_signal = seam_score >= 0.045 or background_score >= 0.82
    if not has_grid_signal:
        return -1.0
    return (
        content_ratio
        + (0.45 if seam_score >= 0.045 else 0.0)
        + (0.30 if background_score >= 0.82 else 0.0)
        + (0.15 if content_count == frame_count else 0.0)
    )


def split_contact_sheet_bytes(image_bytes: bytes, frame_count: int) -> List[bytes]:
    """Split a model-returned contact sheet into individual PNG frames.

    Image APIs normally return one item per frame, but some compatible gateways
    turn ``n=8`` into a single 4x2 preview sheet.  The splitter only activates
    when grid seams and per-cell content both look like a collage; otherwise it
    returns the original bytes unchanged.
    """

    if frame_count <= 1:
        return [image_bytes]
    grids = _contact_sheet_grids(frame_count)
    if not grids:
        return [image_bytes]

    from PIL import Image

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            image = source.convert("RGBA")
            candidates = []
            for columns, rows in grids:
                tile_width = image.width // columns
                tile_height = image.height // rows
                if min(tile_width, tile_height) < 96:
                    continue
                score = _contact_sheet_score(image, columns, rows, frame_count)
                if score >= 0.0:
                    candidates.append((score, columns, rows))
            if not candidates:
                return [image_bytes]

            _, columns, rows = max(candidates)

            frames: List[bytes] = []
            for index in range(frame_count):
                row, column = divmod(index, columns)
                output = BytesIO()
                image.crop(_contact_sheet_tile_bounds(image, columns, rows, column, row)).save(
                    output,
                    format="PNG",
                )
                frames.append(output.getvalue())
            return frames
    except Exception:
        return [image_bytes]


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
    background_mode: Optional[str] = None,
    anchor: str = "center",
    subject_scale: float = 1.0,
    require_transparency: bool = False,
) -> None:
    """Normalize one user photo to a transparent, pose-aligned square PNG.

    ``anchor="bottom"`` keeps the subject's baseline stable across walking
    frames.  A center anchor remains the default for backwards compatibility
    and works better for curled sleeping poses.  ``background_mode`` accepts
    ``none`` or a Pillow-only simple-color fallback. ``require_transparency``
    remains available as an explicit strict mode for callers that require
    native alpha; the server's normal AI path uses the simple fallback so one
    opaque gateway response does not fail the whole package.
    """

    normalize_pet_sequence(
        [source_path],
        [destination_path],
        canvas_size=canvas_size,
        background_mode=background_mode,
        anchor=anchor,
        subject_scale=subject_scale,
        require_transparency=require_transparency,
    )


def _prepare_subject(
    source_path: Path,
    background_mode: Optional[str],
    require_transparency: bool,
):
    """Read, orient, segment, and crop one source without resizing it."""

    from PIL import Image, ImageOps

    with Image.open(source_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")

    image = _remove_background(
        image,
        background_mode=background_mode,
    )
    if require_transparency and image.getchannel("A").getextrema() == (255, 255):
        raise ValueError(
            f"AI 返回的动作帧没有透明 alpha 通道，请在图像模型提示词中启用透明背景: {source_path.name}"
        )
    bbox = image.getchannel("A").getbbox()
    if not bbox:
        raise ValueError(f"无法从照片中读取有效主体: {source_path}")
    return image.crop(bbox)


def normalize_pet_sequence(
    source_paths: Sequence[Path],
    destination_paths: Sequence[Path],
    canvas_size: int = 320,
    background_mode: Optional[str] = None,
    anchor: str = "center",
    subject_scale: float = 1.0,
    require_transparency: bool = False,
) -> None:
    """Normalize a complete action sequence with one shared render contract.

    Every frame is segmented before it is resized, then rendered to the same
    square canvas, subject extent, horizontal center, and optional baseline.
    Processing the sequence together prevents per-frame padding and placement
    decisions from becoming another source of motion jitter.
    """

    from PIL import Image

    if len(source_paths) != len(destination_paths):
        raise ValueError("动作帧源文件和目标文件数量不一致")
    if not source_paths:
        return

    prepared = [
        _prepare_subject(path, background_mode, require_transparency)
        for path in source_paths
    ]
    padding = max(4, int(canvas_size * 0.08))
    try:
        scale = float(subject_scale)
    except (TypeError, ValueError):
        scale = 1.0
    scale = max(0.5, min(1.0, scale))

    # Use one target extent for the whole sequence: no frame is allowed to
    # choose its own crop scale or padding.
    target_extent = max(1, round((canvas_size - padding * 2) * scale))

    for source, destination in zip(prepared, destination_paths):
        image = source.copy()
        image.thumbnail((target_extent, target_extent), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        _paste_subject(canvas, image, padding, anchor)
        destination.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(destination, format="PNG", optimize=True)

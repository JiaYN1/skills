"""Photo normalization for generated desktop pet packages.

The pipeline intentionally has a lightweight fallback so the MVP works with
plain Pillow. The server asks the image model for transparent PNG output and
only uses Pillow here for alpha cleanup, cropping, and frame alignment.
"""

from collections import deque
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _clean_alpha_edges(image, minimum_alpha: int = 8):
    """Remove almost-transparent matte noise and clear RGB fringe pixels.

    Segmentation models generally return a good alpha matte, but RGB values
    from the old background can remain in fully transparent pixels. Those
    values become a visible white/green halo after repeated compositing. Keep
    useful semi-transparent fur while dropping only the very weakest noise.
    """

    from PIL import Image, ImageChops, ImageFilter

    image = image.convert("RGBA")
    alpha = image.getchannel("A")

    # A few compatible image gateways return a green/chroma-key matte around
    # otherwise transparent edges. Remove only green-dominant pixels that are
    # adjacent to transparency, so legitimate colored fur inside the subject
    # is not affected.
    if alpha.getextrema() != (0, 0):
        red, green, blue, _ = image.split()
        transparent = alpha.point(lambda value: 255 if value == 0 else 0)
        edge = transparent.filter(ImageFilter.MaxFilter(5))
        edge = ImageChops.multiply(edge, alpha.point(lambda value: 255 if value else 0))
        strongest_rgb = ImageChops.lighter(red, blue)
        green_excess = ImageChops.subtract(green, strongest_rgb)
        green_mask = green_excess.point(lambda value: 255 if value >= 18 else 0)
        green_edge = ImageChops.multiply(edge, green_mask)
        alpha = ImageChops.subtract(
            alpha,
            green_edge.point(lambda value: 255 if value >= 128 else 0),
        )

    alpha = alpha.point(
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


def has_usable_transparency(source_path: Path, corner_alpha: int = 8) -> bool:
    """Return whether an image has a believable transparent canvas.

    A PNG file extension is not enough: compatible gateways sometimes return
    an RGB image with a checkerboard or green matte painted into it. Require a
    real alpha channel, at least one transparent pixel, and transparent
    corners so the AI cleanup pass can decide whether another attempt is
    needed.
    """

    from PIL import Image

    try:
        with Image.open(source_path) as source:
            if "A" not in source.getbands():
                return False
            alpha = source.convert("RGBA").getchannel("A")
            minimum, maximum = alpha.getextrema()
            if minimum == 255 or maximum == 0:
                return False
            corners = [
                alpha.getpixel((0, 0)),
                alpha.getpixel((max(0, alpha.width - 1), 0)),
                alpha.getpixel((0, max(0, alpha.height - 1))),
                alpha.getpixel((max(0, alpha.width - 1), max(0, alpha.height - 1))),
            ]
            return all(value <= corner_alpha for value in corners)
    except (OSError, ValueError):
        return False


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


CANVAS_MARGIN = 1
MAX_UPSCALE = 2.0
ALIGN_COARSE_SIZE = 48
ALIGN_COARSE_RADIUS = 8
ALIGN_FINE_SIZE = 256
ALIGN_FINE_RADIUS = 6


def _median(values: Sequence[float]) -> float:
    """Return the median of a small numeric sequence."""

    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _working_alpha(alpha, work_size: int):
    """Return a box-averaged alpha copy for cheap overlap scoring.

    The box filter preserves mass, so overlap scores computed on the working
    copy rank candidate offsets the same way the full-resolution masks would.
    """

    from PIL import Image

    factor = max(alpha.width, alpha.height) / float(max(1, work_size))
    if factor <= 1.0:
        return alpha, 1.0
    size = (max(1, round(alpha.width / factor)), max(1, round(alpha.height / factor)))
    return alpha.resize(size, Image.Resampling.BOX), factor


def _alpha_masses(masks: Sequence[Any]) -> List[float]:
    """Return the total alpha mass of every frame.

    Mass tracks the body area and barely reacts to a thin limb, so it is a much
    better body-size signal than a bounding box that a stretched leg inflates.
    """

    from PIL import ImageStat

    return [float(ImageStat.Stat(mask).sum[0]) for mask in masks]


def _overlap_cost(reference, moving, dx: int, dy: int) -> float:
    """Return the mean absolute alpha difference after shifting ``moving``."""

    from PIL import Image, ImageChops, ImageStat

    shifted = Image.new("L", reference.size, 0)
    shifted.paste(moving, (dx, dy))
    return float(ImageStat.Stat(ImageChops.difference(reference, shifted)).mean[0])


def _parabolic_peak(reference, moving, offset: Tuple[int, int], axis: int) -> float:
    """Return a sub-pixel correction for the best overlap offset.

    An integer search alone quantizes the alignment to one working pixel, which
    is several source pixels wide and therefore visible as a small body shift.
    Fitting a parabola through the cost at the optimum and its two neighbours
    recovers the sub-pixel minimum.
    """

    step = (1, 0) if axis == 0 else (0, 1)
    before = _overlap_cost(
        reference, moving, offset[0] - step[0], offset[1] - step[1]
    )
    middle = _overlap_cost(reference, moving, offset[0], offset[1])
    after = _overlap_cost(reference, moving, offset[0] + step[0], offset[1] + step[1])
    denominator = before - 2.0 * middle + after
    if denominator <= 0:
        return 0.0
    return max(-0.5, min(0.5, 0.5 * (before - after) / denominator))


def _best_overlap_offset(reference, moving, radius: int, center: Tuple[int, int]):
    """Return the best overlap offset and its residual cost.

    Scoring the alpha overlap is dominated by the body, because a torso covers
    far more pixels than a leg or a tail. That makes overlap a stable alignment
    signal even while the limbs swing, unlike a bounding box whose center and
    bottom follow the limbs. The residual cost doubles as a quality metric: a
    frame that no longer matches the reference pose cannot be aligned cheaply.
    """

    best_offset = (0, 0)
    best_score = None
    for dy in range(center[1] - radius, center[1] + radius + 1):
        for dx in range(center[0] - radius, center[0] + radius + 1):
            score = _overlap_cost(reference, moving, dx, dy)
            if best_score is None or score < best_score:
                best_score = score
                best_offset = (dx, dy)
    refined = (
        best_offset[0] + _parabolic_peak(reference, moving, best_offset, 0),
        best_offset[1] + _parabolic_peak(reference, moving, best_offset, 1),
    )
    return refined, float(best_score or 0.0)


def _sequence_alignments(
    masks: Sequence[Any],
    boxes: Sequence[Tuple[int, int, int, int]],
    align: bool = True,
    reference_index: Optional[int] = None,
):
    """Return per-frame alignment, the reference frame, and each residual cost.

    Offsets are measured in the source coordinate frame (not inside each tight
    crop, where every frame would look centered), by maximizing the alpha
    overlap against the reference pose. Overlap is dominated by the torso, so a
    swinging leg or tail barely moves the result, while a bounding box follows
    the limbs exactly.

    ``align=False`` is for callers whose frames have no shared source canvas
    (loose still photos of different sizes): there is nothing to register them
    against, so each frame is anchored by its own alpha box instead, which keeps
    the shared scale without inventing a translation out of unrelated photos.

    ``reference_index`` pins the reference frame, which is how an edit keeps the
    rest of a sequence exactly where it was.
    """

    from PIL import Image

    areas = [(box[2] - box[0]) * (box[3] - box[1]) for box in boxes]
    if reference_index is None or not 0 <= int(reference_index) < len(boxes):
        reference_index = min(
            range(len(areas)),
            key=lambda index: abs(areas[index] - _median(areas)),
        )
    reference_index = int(reference_index)
    if not align:
        reference_box = boxes[reference_index]
        offsets = [
            (reference_box[0] - box[0], reference_box[1] - box[1]) for box in boxes
        ]
        return offsets, reference_index, [0.0 for _ in masks]
    if len(masks) < 2:
        return [(0, 0) for _ in masks], reference_index, [0.0 for _ in masks]

    extent = (
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )
    absolute: List[Any] = []
    for mask, box in zip(masks, boxes):
        layer = Image.new("L", extent, 0)
        layer.paste(mask, (box[0], box[1]))
        absolute.append(layer)

    offsets: List[Tuple[int, int]] = [(0, 0) for _ in masks]
    costs: List[float] = [0.0 for _ in masks]
    coarse_reference, coarse_factor = _working_alpha(absolute[reference_index], ALIGN_COARSE_SIZE)
    fine_reference, fine_factor = _working_alpha(absolute[reference_index], ALIGN_FINE_SIZE)
    reference_box = boxes[reference_index]
    reference_center = (
        (reference_box[0] + reference_box[2]) / 2.0,
        (reference_box[1] + reference_box[3]) / 2.0,
    )

    for index, mask in enumerate(absolute):
        if index == reference_index:
            continue
        box = boxes[index]
        # The silhouette center is only a starting guess: it is off by half of
        # whatever a limb adds to the silhouette, which the overlap search then
        # corrects.
        guess = (
            round((reference_center[0] - (box[0] + box[2]) / 2.0) / coarse_factor),
            round((reference_center[1] - (box[1] + box[3]) / 2.0) / coarse_factor),
        )
        coarse, _ = _working_alpha(mask, ALIGN_COARSE_SIZE)
        coarse_offset, _coarse_cost = _best_overlap_offset(
            coarse_reference, coarse, ALIGN_COARSE_RADIUS, guess
        )
        fine, _ = _working_alpha(mask, ALIGN_FINE_SIZE)
        fine_center = (
            round(coarse_offset[0] * coarse_factor / fine_factor),
            round(coarse_offset[1] * coarse_factor / fine_factor),
        )
        fine_offset, fine_cost = _best_overlap_offset(
            fine_reference, fine, ALIGN_FINE_RADIUS, fine_center
        )
        offsets[index] = (
            int(round(fine_offset[0] * fine_factor)),
            int(round(fine_offset[1] * fine_factor)),
        )
        costs[index] = max(0.0, float(fine_cost))
    return offsets, reference_index, costs


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
    """Read, orient, segment, and crop one source without resizing it.

    Returns the cropped subject, its box in the source image, and the source
    size. Sequence alignment needs the box so it can work in one shared source
    coordinate frame instead of per-frame crop coordinates (where every frame
    looks centered), and the size so it can tell whether the frames really do
    share a canvas.
    """

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
    return image.crop(bbox), bbox, image.size


def normalize_pet_sequence(
    source_paths: Sequence[Path],
    destination_paths: Sequence[Path],
    canvas_size: int = 320,
    background_mode: Optional[str] = None,
    anchor: str = "center",
    subject_scale: float = 1.0,
    require_transparency: bool = False,
    report: Optional[Dict[str, Any]] = None,
    contract: Optional[Dict[str, Any]] = None,
) -> None:
    """Normalize a complete action sequence with one shared render contract.

    Every frame is segmented, then the whole sequence is measured together
    before anything is written, because the image model is not asked for
    pixel-exact frame alignment. Three rules keep the pet from jittering:

    1. **One scale for the whole sequence.** Scaling each frame so that its own
       bounding box fits a target extent makes the pet grow and shrink whenever
       a leg or a tail stretches that box, which a side-view walk cycle does on
       almost every frame. The scale here is the median fit of the sequence, and
       it is only ever reduced as one global factor, so every frame keeps the
       same body size.
    2. **Alignment by overlap, not by bounding box.** The bounding box bottom
       is the lowest paw of that frame and its center moves with the limbs, so
       anchoring on it bobs and slides the torso inside the canvas. Each frame is
       instead registered onto the median-area reference frame by maximizing
       alpha overlap (``_sequence_alignments``), which is dominated by the body.
    3. **One global placement.** The anchor decides where the reference frame
       sits; every other frame reuses that placement plus its own registration
       offset. Overflow is corrected by shifting or scaling the entire sequence
       together, never a single frame.

    A frame whose pose is a genuine outlier for the sequence can therefore end up
    clipped at the canvas edge, but it is never rescaled or nudged on its own:
    deleting or regenerating such a frame is a QA decision, not a scaling one.

    ``report``, when a dict is passed, is filled with the measured numbers of
    this run (render scale, reference frame, per-frame mass, registration
    offset, registration residual, clipped flag, and the placements a later edit
    can reuse). ``contract`` is the previous report of the same sequence:
    passing it pins the render scale and the reference frame, so editing one
    frame of a role leaves every other frame exactly where it was instead of
    rescaling the whole action. A pinned run never rescales or shifts the other
    frames; it only reports what it had to clip.
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
    if not prepared:
        return

    padding = max(4, int(canvas_size * 0.08))
    try:
        subject_scale_value = float(subject_scale)
    except (TypeError, ValueError):
        subject_scale_value = 1.0
    subject_scale_value = max(0.5, min(1.0, subject_scale_value))

    target_extent = max(1, round((canvas_size - padding * 2) * subject_scale_value))

    normalized_anchor = str(anchor or "center").strip().lower()
    bottom_anchor = normalized_anchor in {"bottom", "baseline", "bottom-center"}
    top_anchor = normalized_anchor == "top"

    images = [image for image, _box, _size in prepared]
    boxes = [box for _image, box, _size in prepared]
    source_sizes = {size for _image, _box, size in prepared}
    masks = [image.getchannel("A") for image in images]

    pinned_scale: Optional[float] = None
    pinned_reference: Optional[int] = None
    stored_placements: List[Any] = []
    frozen_base: Optional[Tuple[int, int]] = None
    if isinstance(contract, dict):
        try:
            candidate_scale = float(contract.get("render_scale"))
            if candidate_scale > 0:
                pinned_scale = candidate_scale
        except (TypeError, ValueError):
            pinned_scale = None
        candidate_index = contract.get("reference_index")
        if isinstance(candidate_index, int):
            pinned_reference = candidate_index
        stored = contract.get("placements")
        if isinstance(stored, (list, tuple)):
            stored_placements = list(stored)
        stored_canvas = contract.get("canvas_size")
        if stored_canvas is not None and int(stored_canvas or 0) != int(canvas_size):
            # The pinned geometry belongs to a different canvas size.
            pinned_scale = None
            frozen_base = None
        elif (
            pinned_scale is not None
            and pinned_reference is not None
            and 0 <= pinned_reference < len(stored_placements)
            and isinstance(stored_placements[pinned_reference], (list, tuple))
            and len(stored_placements[pinned_reference]) == 2
        ):
            # The reference frame's own placement *is* the anchor position, so
            # freezing it keeps the whole sequence in place even when the edited
            # frame happens to be the reference frame itself.
            reference_placement = stored_placements[pinned_reference]
            frozen_base = (int(reference_placement[0]), int(reference_placement[1]))

    # Generated action frames all arrive on one shared 1024x1024 canvas, where
    # registration is meaningful. Loose still photos of unrelated sizes have no
    # shared frame to align in, so they only get the shared scale.
    alignments, reference_index, alignment_costs = _sequence_alignments(
        masks,
        boxes,
        align=len(source_sizes) == 1,
        reference_index=pinned_reference,
    )
    reference_box = boxes[reference_index]

    # One render scale for every frame: the median pose defines how large the
    # pet should be, and a small source photo is never blown up into mush. A
    # pinned contract reuses the scale an earlier run already shipped.
    fit_scale = _median(
        [
            target_extent / max(1, max(box[2] - box[0], box[3] - box[1]))
            for box in boxes
        ]
    )
    if pinned_scale is not None:
        render_scale = max(0.01, pinned_scale)
    else:
        render_scale = max(0.01, min(fit_scale, MAX_UPSCALE))

    def place_all(scale: float):
        reference_width = max(1, round((reference_box[2] - reference_box[0]) * scale))
        reference_height = max(1, round((reference_box[3] - reference_box[1]) * scale))
        if frozen_base is not None:
            base_left, base_top = frozen_base
        else:
            base_left = (canvas_size - reference_width) // 2
            if bottom_anchor:
                base_top = canvas_size - padding - reference_height
            elif top_anchor:
                base_top = padding
            else:
                base_top = (canvas_size - reference_height) // 2
        placements = []
        for index, box in enumerate(boxes):
            width = max(1, round((box[2] - box[0]) * scale))
            height = max(1, round((box[3] - box[1]) * scale))
            # The reference frame defines the canvas mapping; every other frame
            # is that same mapping plus its own overlap-registration offset.
            left = base_left + round(
                (box[0] + alignments[index][0] - reference_box[0]) * scale
            )
            top = base_top + round(
                (box[1] + alignments[index][1] - reference_box[1]) * scale
            )
            placements.append((left, top, width, height))
        lefts = [item[0] for item in placements]
        tops = [item[1] for item in placements]
        rights = [item[0] + item[2] for item in placements]
        bottoms = [item[1] + item[3] for item in placements]
        return placements, (min(lefts), min(tops), max(rights), max(bottoms))

    # A single stretched pose can make the whole sequence too large to fit at
    # the median scale. Shrink everything by one shared factor instead of
    # shrinking the outlier alone. A pinned run may not do that: the whole point
    # of an edit is that the other frames keep the placement they already have,
    # so an oversized new frame is reported as clipped instead.
    if pinned_scale is None:
        for _ in range(3):
            placements, union = place_all(render_scale)
            union_width = union[2] - union[0]
            union_height = union[3] - union[1]
            available = canvas_size - CANVAS_MARGIN * 2
            if union_width <= available and union_height <= available:
                break
            render_scale *= available / max(1, union_width, union_height)
            render_scale = max(0.01, render_scale)

    placements, union = place_all(render_scale)
    # Keep the sequence inside the canvas with one shared nudge, preserving the
    # relative placement of every frame.
    shift_x = 0
    shift_y = 0
    if pinned_scale is None:
        if union[0] < CANVAS_MARGIN:
            shift_x = CANVAS_MARGIN - union[0]
        elif union[2] > canvas_size - CANVAS_MARGIN:
            shift_x = canvas_size - CANVAS_MARGIN - union[2]
        if union[1] < CANVAS_MARGIN:
            shift_y = CANVAS_MARGIN - union[1]
        elif union[3] > canvas_size - CANVAS_MARGIN:
            shift_y = canvas_size - CANVAS_MARGIN - union[3]

    final_placements = [
        (left + shift_x, top + shift_y) for left, top, _width, _height in placements
    ]
    clipped = [
        left < 0
        or top < 0
        or left + placements[index][2] > canvas_size
        or top + placements[index][3] > canvas_size
        for index, (left, top) in enumerate(final_placements)
    ]
    if report is not None:
        report.clear()
        report.update(
            {
                "canvas_size": canvas_size,
                "anchor": normalized_anchor,
                "render_scale": round(render_scale, 6),
                "reference_index": int(reference_index),
                "pinned": pinned_scale is not None,
                "frames": [
                    {
                        "index": index,
                        "box": [int(value) for value in box],
                        "mass": float(mask_sum),
                        "alignment": [int(alignments[index][0]), int(alignments[index][1])],
                        "alignment_cost": round(alignment_costs[index], 4),
                        "clipped": bool(clipped[index]),
                    }
                    for index, (box, mask_sum) in enumerate(
                        zip(boxes, _alpha_masses(masks))
                    )
                ],
                "placements": [[int(left), int(top)] for left, top in final_placements],
            }
        )
        if stored_placements:
            drifted = [
                index
                for index, (left, top) in enumerate(final_placements)
                if index < len(stored_placements)
                and isinstance(stored_placements[index], (list, tuple))
                and len(stored_placements[index]) == 2
                and (
                    abs(int(stored_placements[index][0]) - left) > 1
                    or abs(int(stored_placements[index][1]) - top) > 1
                )
            ]
            report["contract_drift"] = drifted

    for image, (left, top, width, height), destination in zip(
        images, placements, destination_paths
    ):
        rendered = image.resize((width, height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        canvas.alpha_composite(rendered, (left + shift_x, top + shift_y))
        destination.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(destination, format="PNG", optimize=True)

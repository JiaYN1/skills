"""Sprite atlases: deterministic grids, splitting, checks, mirroring, and a CLI.

A single generation that returns a whole action as one sheet is far cheaper than
one request per frame and, more importantly, it cannot drift: every cell comes
from the same generation, so body size, colours, and lighting agree by
construction. What such a sheet needs is the opposite of guessing - the grid has
to be stated, the split has to be exact, and the result has to be checked before
it is trusted.

The same functions are also the repair path for an existing package. When one
frame drifts, when a gateway returned a whole action as one sheet, or when a
package was edited by hand, these commands fix it locally and deterministically:

    python pet_atlas.py validate --package .\\我的宠物
    python pet_atlas.py grid-check --image sheet.png --frames 10
    python pet_atlas.py split --image sheet.png --frames 10 --out .\\frames
    python pet_atlas.py flip-rows --image sheet.png --rows 2 --out mirrored.png
    python pet_atlas.py repack --package .\\我的宠物

Everything here is Pillow-only and offline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

APP_ROOT = Path(__file__).resolve().parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

#: A cell whose visible mass is below this fraction of the median is empty.
EMPTY_CELL_RATIO = 0.18
#: Cells may not disagree on subject size forever; sqrt(mass/median) outside this
#: range is reported as an inconsistent cell.
CELL_SCALE_TOLERANCE = 0.4
#: How many pixels along a cell edge are checked for cut-through content.
EDGE_MARGIN = 1
#: A cell smaller than this is too coarse to be a usable sprite.
MIN_CELL_SIZE = 48

#: Preferred layouts, chosen so the cells stay close to square.
_PREFERRED_GRIDS = {
    1: (1, 1),
    2: (2, 1),
    3: (3, 1),
    4: (2, 2),
    5: (5, 1),
    6: (3, 2),
    7: (7, 1),
    8: (4, 2),
    9: (3, 3),
    10: (5, 2),
    11: (11, 1),
    12: (4, 3),
    14: (7, 2),
    15: (5, 3),
    16: (4, 4),
    18: (6, 3),
    20: (5, 4),
    24: (6, 4),
    25: (5, 5),
    28: (7, 4),
    30: (6, 5),
    32: (8, 4),
}


def grid_for(frame_count: int) -> Tuple[int, int]:
    """Return the ``(columns, rows)`` layout to request for a frame count."""

    count = max(1, int(frame_count))
    if count in _PREFERRED_GRIDS:
        return _PREFERRED_GRIDS[count]
    # Fall back to the near-square layout that fits, preferring extra columns
    # over extra rows because pet cycles are usually read left to right.
    columns = 1
    while columns * columns < count:
        columns += 1
    rows = (count + columns - 1) // columns
    return columns, rows


def cell_bounds(
    image, columns: int, rows: int, column: int, row: int
) -> Tuple[int, int, int, int]:
    """Return the pixel box of one cell, using exact integer divisions."""

    columns = max(1, int(columns))
    rows = max(1, int(rows))
    left = image.width * int(column) // columns
    right = image.width * (int(column) + 1) // columns
    top = image.height * int(row) // rows
    bottom = image.height * (int(row) + 1) // rows
    return left, top, right, bottom


def split_atlas(image, columns: int, rows: int, frame_count: Optional[int] = None):
    """Split a sheet into frames in reading order.

    Returns the list of cell images; the caller decides what to do with a short
    or malformed grid, because ``grid_check`` is what reports that.
    """

    columns = max(1, int(columns))
    rows = max(1, int(rows))
    total = columns * rows
    wanted = total if frame_count is None else max(1, min(total, int(frame_count)))
    frames = []
    for index in range(wanted):
        row, column = divmod(index, columns)
        frames.append(image.crop(cell_bounds(image, columns, rows, column, row)))
    return frames


def _cell_report(cell, edge_margin: int = EDGE_MARGIN) -> Dict[str, Any]:
    """Measure one cell: visible mass, alpha box, and edge contact."""

    from PIL import ImageStat

    alpha = cell.getchannel("A")
    mass = float(ImageStat.Stat(alpha).sum[0])
    box = alpha.getbbox()
    touches_edge = False
    if box is not None and int(edge_margin) > 0:
        margin = min(int(edge_margin), max(1, min(cell.width, cell.height) // 8))
        strips = (
            (0, 0, cell.width, margin),
            (0, cell.height - margin, cell.width, cell.height),
            (0, 0, margin, cell.height),
            (cell.width - margin, 0, cell.width, cell.height),
        )
        for strip in strips:
            if alpha.crop(strip).getbbox() is not None:
                touches_edge = True
                break
    return {
        "mass": mass,
        "box": list(box) if box else None,
        "touches_edge": touches_edge,
    }


def grid_check(
    image,
    columns: int,
    rows: int,
    frame_count: Optional[int] = None,
    edge_margin: int = EDGE_MARGIN,
    empty_ratio: float = EMPTY_CELL_RATIO,
    scale_tolerance: float = CELL_SCALE_TOLERANCE,
) -> Dict[str, Any]:
    """Validate a sprite sheet before its cells are trusted.

    Reports the per-cell measurements plus a ``problems`` list: a grid that is
    too fine, a sheet without alpha (nothing to check emptiness against), empty
    or edge-crossing cells, and cells whose subject is a different size from the
    rest. An empty ``problems`` list means the sheet can be split as-is.
    """

    columns = max(1, int(columns))
    rows = max(1, int(rows))
    total = columns * rows
    wanted = total if frame_count is None else max(1, int(frame_count))
    problems: List[str] = []
    cell_width = image.width // columns
    cell_height = image.height // rows
    if min(cell_width, cell_height) < MIN_CELL_SIZE:
        problems.append(
            f"格子太小（{cell_width}x{cell_height}），至少需要 {MIN_CELL_SIZE}px"
        )
    cells = split_atlas(image, columns, rows, wanted)
    reports = [_cell_report(cell, edge_margin) for cell in cells]
    masses = [report["mass"] for report in reports]
    visible = [mass for mass in masses if mass > 0]
    median_mass = sorted(visible)[len(visible) // 2] if visible else 0.0
    has_alpha = any(cell.getchannel("A").getextrema()[0] < 255 for cell in cells)
    if not has_alpha:
        problems.append("图片没有透明像素，无法验证格子是否为空或越界")
    if wanted < total:
        problems.append(f"只需要 {wanted} 格，但网格有 {total} 格")
    for index, report in enumerate(reports):
        mass = float(report["mass"])
        if median_mass <= 0:
            problems.append(f"第 {index + 1} 格没有任何内容")
            continue
        if mass < median_mass * max(0.0, float(empty_ratio)):
            problems.append(f"第 {index + 1} 格内容为空或几乎不可见")
        else:
            scale = (mass / median_mass) ** 0.5
            if abs(scale - 1.0) > float(scale_tolerance):
                problems.append(
                    f"第 {index + 1} 格主体大小与同表不一致（{scale:.2f}x）"
                )
        if report["touches_edge"]:
            problems.append(f"第 {index + 1} 格内容碰到格子边缘，切图会切断主体")
    return {
        "columns": columns,
        "rows": rows,
        "frame_count": wanted,
        "cell_size": [cell_width, cell_height],
        "has_alpha": has_alpha,
        "median_mass": round(median_mass, 2),
        "cells": [
            {
                "index": index,
                "mass": round(float(report["mass"]), 2),
                "box": report["box"],
                "touches_edge": bool(report["touches_edge"]),
            }
            for index, report in enumerate(reports)
        ],
        "problems": problems,
        "ok": not problems,
    }


def trim_cell(cell, padding: int = 1):
    """Crop a cell to its content with a small transparent padding."""

    alpha = cell.getchannel("A")
    box = alpha.getbbox()
    if box is None:
        return cell
    left = max(0, box[0] - padding)
    top = max(0, box[1] - padding)
    right = min(cell.width, box[2] + padding)
    bottom = min(cell.height, box[3] + padding)
    return cell.crop((left, top, right, bottom))


def mirror_rows(image, rows: int):
    """Mirror every row of a sheet horizontally.

    A cycle that reads left to right becomes the same cycle facing the other
    way, while the row layout stays put, so one sheet can serve both walk
    directions.
    """

    from PIL import Image

    rows = max(1, int(rows))
    mirrored = Image.new("RGBA", image.size, (0, 0, 0, 0))
    for row in range(rows):
        top = image.height * row // rows
        bottom = image.height * (row + 1) // rows
        if bottom <= top:
            continue
        strip = image.crop((0, top, image.width, bottom))
        mirrored.paste(strip.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (0, top))
    return mirrored


def save_frames(frames: Sequence[Any], destinations: Sequence[Path]) -> List[Path]:
    """Save split cells to numbered paths, creating parent directories."""

    written: List[Path] = []
    for frame, destination in zip(frames, destinations):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        frame.save(destination, format="PNG", optimize=True)
        written.append(destination)
    return written


def load_sheet(path: Path):
    """Load a sheet as RGBA."""

    from PIL import Image

    with Image.open(path) as source:
        return source.convert("RGBA")


def _role_assets(package_dir: Path, role: str) -> List[Path]:
    assets_dir = package_dir / "assets"

    def order(path: Path) -> int:
        suffix = path.stem.split("_")[-1]
        return int(suffix) if suffix.isdigit() else 0

    return sorted(assets_dir.glob(f"{role}_*.png"), key=order)


def _load_config(package_dir: Path) -> Dict[str, Any]:
    config_path = package_dir / "pet_config.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"找不到 {config_path}")
    return json.loads(config_path.read_text(encoding="utf-8"))


def _background_mode(config: Dict[str, Any]) -> Optional[str]:
    generation = config.get("generation")
    if isinstance(generation, dict) and generation.get("background_removal"):
        return "simple"
    return None


def role_anchor(role: str) -> str:
    """Return the anchor a role was generated with."""

    return "center" if role == "sleep" else "bottom"


def command_validate(args: argparse.Namespace) -> int:
    """Report the per-frame quality flags of every role in a package."""

    import tempfile

    import pet_quality
    from pet_assets import normalize_pet_sequence
    from pet_common import ROLES

    package_dir = Path(args.package).expanduser().resolve()
    config = _load_config(package_dir)
    background_mode = _background_mode(config)
    exit_code = 0
    # Validation is read-only: the frames are normalized into a scratch
    # directory just to measure them, never written back over the package.
    with tempfile.TemporaryDirectory() as scratch:
        scratch_dir = Path(scratch)
        for role in ROLES:
            paths = _role_assets(package_dir, role)
            if not paths:
                continue
            report: Dict[str, Any] = {}
            normalize_pet_sequence(
                paths,
                [scratch_dir / f"{role}_{index}.png" for index in range(len(paths))],
                canvas_size=320,
                background_mode=background_mode,
                anchor=role_anchor(role),
                subject_scale=0.96,
                report=report,
            )
            verdicts = pet_quality.judge_sequence(
                report,
                pet_quality.subject_signatures(paths, background_mode=background_mode),
            )
            print(f"[{role}] {len(paths)} 帧，渲染比例 {report.get('render_scale')}")
            if not verdicts:
                print("  帧数太少，跳过质量检查")
                continue
            flagged = [verdict for verdict in verdicts if verdict["reasons"]]
            if not flagged:
                print("  质量检查通过")
                continue
            exit_code = 1
            for verdict in flagged:
                labels = "、".join(
                    pet_quality.REASON_LABELS.get(reason, reason)
                    for reason in verdict["reasons"]
                )
                print(f"  第 {verdict['index'] + 1} 帧：{labels}")
            if args.json:
                print(json.dumps(verdicts, ensure_ascii=False, indent=2))
    return exit_code


def command_grid_check(args: argparse.Namespace) -> int:
    """Validate a sprite sheet grid before splitting it."""

    image = load_sheet(Path(args.image).expanduser())
    columns = args.columns
    rows = args.rows
    if not columns or not rows:
        columns, rows = grid_for(args.frames or max(1, columns * rows))
    report = grid_check(image, columns, rows, args.frames or None)
    print(
        f"网格 {columns}x{rows}，格子 {report['cell_size'][0]}x{report['cell_size'][1]}，"
        f"需要 {report['frame_count']} 帧"
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["ok"]:
        print("网格检查通过")
        return 0
    for problem in report["problems"]:
        print(f"  - {problem}")
    return 1


def command_split(args: argparse.Namespace) -> int:
    """Split a sheet into numbered frames."""

    image_path = Path(args.image).expanduser()
    image = load_sheet(image_path)
    columns = args.columns
    rows = args.rows
    if not columns or not rows:
        columns, rows = grid_for(args.frames)
    report = grid_check(image, columns, rows, args.frames)
    if not report["ok"] and not args.force:
        print("网格检查未通过，已中止（加 --force 可强制切图）：")
        for problem in report["problems"]:
            print(f"  - {problem}")
        return 1
    frames = split_atlas(image, columns, rows, args.frames)
    if args.trim:
        frames = [trim_cell(frame) for frame in frames]
    output_dir = Path(args.out).expanduser()
    prefix = args.prefix or image_path.stem
    destinations = [output_dir / f"{prefix}_{index}.png" for index in range(len(frames))]
    written = save_frames(frames, destinations)
    print(f"已写出 {len(written)} 帧到 {output_dir}")
    return 0


def command_flip_rows(args: argparse.Namespace) -> int:
    """Mirror every row of a sheet for the opposite walk direction."""

    image_path = Path(args.image).expanduser()
    image = load_sheet(image_path)
    rows = args.rows or grid_for(args.frames or 1)[1]
    mirrored = mirror_rows(image, rows)
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    mirrored.save(out, format="PNG", optimize=True)
    print(f"已写出镜像表 {out}（{rows} 行）")
    return 0


def command_repack(args: argparse.Namespace) -> int:
    """Re-normalize every role of an existing package in place.

    This is the repair path for a package whose frames were edited by hand: it
    applies the same shared scale and registration the generator uses, so the
    action stops jittering without regenerating anything.
    """

    from pet_assets import normalize_pet_sequence
    from pet_common import ROLES

    package_dir = Path(args.package).expanduser().resolve()
    config = _load_config(package_dir)
    background_mode = _background_mode(config)
    changed = 0
    for role in ROLES:
        paths = _role_assets(package_dir, role)
        if len(paths) < 2:
            continue
        report: Dict[str, Any] = {}
        normalize_pet_sequence(
            paths,
            paths,
            canvas_size=320,
            background_mode=background_mode,
            anchor=role_anchor(role),
            subject_scale=0.96,
            report=report,
        )
        changed += 1
        print(f"[{role}] 已按统一比例 {report.get('render_scale')} 重新对齐 {len(paths)} 帧")
    if not changed:
        print("没有需要重新对齐的多帧动作")
        return 0
    print("提示：重新打包 zip 或重新生成 exe 后生效")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="桌面宠物资源包工具：校验、切图、镜像、重新对齐",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="检查资源包每帧质量")
    validate.add_argument("--package", required=True, help="资源包目录")
    validate.add_argument("--json", action="store_true", help="输出详细指标")
    validate.set_defaults(func=command_validate)

    grid_check_parser = subparsers.add_parser("grid-check", help="检查拼图网格是否可用")
    grid_check_parser.add_argument("--image", required=True)
    grid_check_parser.add_argument("--frames", type=int, default=0)
    grid_check_parser.add_argument("--columns", type=int, default=0)
    grid_check_parser.add_argument("--rows", type=int, default=0)
    grid_check_parser.add_argument("--json", action="store_true")
    grid_check_parser.set_defaults(func=command_grid_check)

    split = subparsers.add_parser("split", help="把拼图切成逐帧 PNG")
    split.add_argument("--image", required=True)
    split.add_argument("--frames", type=int, required=True)
    split.add_argument("--columns", type=int, default=0)
    split.add_argument("--rows", type=int, default=0)
    split.add_argument("--out", required=True, help="输出目录")
    split.add_argument("--prefix", default="")
    split.add_argument("--trim", action="store_true", help="去掉每格多余透明边")
    split.add_argument("--force", action="store_true", help="网格检查失败也切图")
    split.set_defaults(func=command_split)

    flip = subparsers.add_parser("flip-rows", help="逐行水平镜像，得到反方向动作")
    flip.add_argument("--image", required=True)
    flip.add_argument("--rows", type=int, default=0)
    flip.add_argument("--frames", type=int, default=0)
    flip.add_argument("--out", required=True)
    flip.set_defaults(func=command_flip_rows)

    repack = subparsers.add_parser("repack", help="按统一比例重新对齐资源包")
    repack.add_argument("--package", required=True)
    repack.set_defaults(func=command_repack)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

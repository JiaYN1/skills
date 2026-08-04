"""Transparent Windows desktop pet runtime.

The same file is used for previews and by PyInstaller-generated executables.
Assets are read relative to ``pet_config.json`` or PyInstaller's bundle
directory, so a one-file executable can carry the complete pet package.
"""

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


class PetConfig:
    def __init__(
        self,
        name: str,
        scale: float,
        speed: float,
        always_on_top: bool,
        sleep_after_seconds: int,
        assets: Dict[str, List[str]],
        animation: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.scale = scale
        self.speed = speed
        self.always_on_top = always_on_top
        self.sleep_after_seconds = sleep_after_seconds
        self.assets = assets
        self.animation = animation or {}

    @classmethod
    def from_file(cls, path: Path) -> "PetConfig":
        data: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            name=str(data.get("name") or "我的宠物"),
            scale=max(0.4, min(2.0, float(data.get("scale", 1.0)))),
            speed=max(0.5, min(10.0, float(data.get("speed", 2.2)))),
            always_on_top=bool(data.get("always_on_top", True)),
            sleep_after_seconds=max(10, int(data.get("sleep_after_seconds", 60))),
            assets={
                str(role): [str(item) for item in items]
                for role, items in (data.get("assets") or {}).items()
                if isinstance(items, list)
            },
            animation={
                str(key): value
                for key, value in (data.get("animation") or {}).items()
            },
        )


def bundle_directory() -> Path:
    """Return the directory containing bundled data for source or one-file runs."""

    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def load_bundle(config_path: Optional[Union[str, Path]] = None) -> Tuple[PetConfig, Path]:
    if config_path is None:
        path = bundle_directory() / "pet_config.json"
    else:
        path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"找不到配置文件: {path}")
    return PetConfig.from_file(path), path.parent


class PetWindow:
    """A small state-machine-driven transparent Tk window."""

    TRANSPARENT = "#01fef0"
    TICK_MS = 50

    def __init__(self, config: PetConfig, base_dir: Path):
        import tkinter as tk
        from PIL import Image, ImageEnhance, ImageTk

        self.tk = tk
        self.Image = Image
        self.ImageEnhance = ImageEnhance
        self.ImageTk = ImageTk
        self.config = config
        self.base_dir = base_dir
        self.animation_mode = str(config.animation.get("mode") or "hybrid").lower()
        try:
            self.animation_fps = max(1.0, min(60.0, float(config.animation.get("fps", 12))))
        except (TypeError, ValueError):
            self.animation_fps = 12.0
        self.frame_interval = 1.0 / self.animation_fps
        # Poll at least twice per frame so a high-FPS configuration does not
        # skip frames. The image item itself is updated in place below; this is
        # important because deleting/recreating a Tk canvas item causes a
        # visible flash on transparent windows.
        self.tick_ms = max(8, min(50, round(500.0 / self.animation_fps)))
        self.next_frame_at = 0.0
        self.root = tk.Tk()
        self.root.title(config.name)
        self.root.overrideredirect(True)
        self.root.configure(bg=self.TRANSPARENT)
        self.root.attributes("-topmost", config.always_on_top)
        try:
            self.root.wm_attributes("-transparentcolor", self.TRANSPARENT)
        except tk.TclError:
            # Linux/macOS preview fallback; Windows supports this attribute.
            self.root.attributes("-alpha", 1.0)

        try:
            self.root.wm_attributes("-toolwindow", True)
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(
            self.root,
            bg=self.TRANSPARENT,
            bd=0,
            highlightthickness=0,
            relief="flat",
        )
        self.canvas.pack(fill="both", expand=True)

        self.skeleton_manifest = self._load_skeleton_manifest()
        self.frames = self._load_animation_frames()
        self.state = "idle"
        self.state_started = time.monotonic()
        self.state_duration = 0.0
        self.frame_index = 0
        self.direction = random.choice((-1, 1))
        self.last_interaction = time.monotonic()
        self.x = 0
        self.y = 0
        self.drag_start: Optional[Tuple[int, int, int, int]] = None
        self.dragged = False
        self.photo_image = None
        self.image_item = None

        self._build_menu()
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._show_menu)
        self.root.bind("<Escape>", lambda _event: self.close())

        self.root.update_idletasks()
        first_frame = self.frames["idle"][0]
        width, height = first_frame.width(), first_frame.height()
        self.canvas.configure(width=width, height=height)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.x = max(0, (screen_width - width) // 2)
        self.y = max(0, screen_height - height - 100)
        self._set_geometry(width, height)
        self.photo_image = first_frame
        self.image_item = self.canvas.create_image(
            0,
            0,
            anchor="nw",
            image=first_frame,
            tags="pet",
        )

    def _resolve_asset(self, relative_path: str) -> Path:
        path = Path(relative_path)
        return path if path.is_absolute() else self.base_dir / path

    def _source_images(self) -> Dict[str, List[Any]]:
        sources: Dict[str, List[Any]] = {}
        for role in ("idle", "walk", "sleep", "react"):
            loaded = []
            for relative_path in self.config.assets.get(role, []):
                path = self._resolve_asset(relative_path)
                if not path.exists():
                    continue
                try:
                    with self.Image.open(path) as image:
                        loaded.append(image.convert("RGBA"))
                except Exception:
                    continue
            if not loaded:
                for fallback_role in ("idle", "react", "walk", "sleep"):
                    if sources.get(fallback_role):
                        loaded = [image.copy() for image in sources[fallback_role]]
                        break
            sources[role] = loaded
        if not sources["idle"]:
            raise FileNotFoundError("配置中没有可用的宠物 PNG 资源")
        return sources

    def _load_skeleton_manifest(self) -> Dict[str, Any]:
        relative_path = self.config.animation.get("skeleton_path")
        if not relative_path:
            return {}
        path = self._resolve_asset(str(relative_path))
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _paste_center(canvas, image, y_offset: int = 0):
        left = (canvas.width - image.width) // 2
        top = (canvas.height - image.height) // 2 + y_offset
        canvas.alpha_composite(image, (left, top))

    def _fit_image(self, image):
        max_size = 320
        if self.config.scale != 1.0:
            max_size = max(96, min(640, round(max_size * self.config.scale)))
        image = image.copy()
        image.thumbnail((max_size, max_size), self.Image.Resampling.LANCZOS)
        canvas_size = max(image.width, image.height, max_size)
        canvas = self.Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        self._paste_center(canvas, image)
        return canvas

    def _animation_frame(self, source, state: str, step: int):
        """Create subtle procedural motion around the user's photo."""

        source = self._fit_image(source)
        width, height = source.size
        if state == "idle":
            amount = 1.0 + math.sin(step / 8.0 * math.pi * 2) * 0.018
            y_offset = round(math.sin(step / 8.0 * math.pi * 2) * 2)
            resized = source.resize(
                (max(1, round(width * amount)), max(1, round(height * amount))),
                self.Image.Resampling.LANCZOS,
            )
            canvas = self.Image.new("RGBA", (width, height), (0, 0, 0, 0))
            self._paste_center(canvas, resized, y_offset)
            return canvas

        if state == "walk":
            amount = 1.0 + (0.018 if step % 2 else -0.012)
            y_offset = 3 if step % 2 else -2
            resized = source.resize(
                (max(1, round(width * amount)), max(1, round(height * amount))),
                self.Image.Resampling.LANCZOS,
            )
            canvas = self.Image.new("RGBA", (width, height), (0, 0, 0, 0))
            self._paste_center(canvas, resized, y_offset)
            return canvas

        if state == "sleep":
            angle = -5 + step * 2.5
            rotated = source.rotate(angle, resample=self.Image.Resampling.BICUBIC, expand=False)
            rotated = self.ImageEnhance.Brightness(rotated).enhance(0.86)
            return rotated

        # click reaction: a short squash-and-stretch bounce
        scale_values = (1.0, 1.08, 0.94, 1.04, 1.0, 1.0)
        amount = scale_values[step % len(scale_values)]
        resized = source.resize(
            (max(1, round(width * amount)), max(1, round(height * amount))),
            self.Image.Resampling.LANCZOS,
        )
        canvas = self.Image.new("RGBA", (width, height), (0, 0, 0, 0))
        self._paste_center(canvas, resized, -round((amount - 1) * 18))
        return canvas

    def _skeleton_frame(self, source, state: str, step: int):
        """Apply the root bone timeline to a base sprite when skeleton mode is active."""

        animations = self.skeleton_manifest.get("animations")
        if not isinstance(animations, dict):
            return None
        sequence = animations.get(state)
        if not isinstance(sequence, dict):
            return None
        frames = sequence.get("frames")
        if not isinstance(frames, list) or not frames:
            return None
        record = frames[step % len(frames)]
        if not isinstance(record, dict):
            return None
        bones = record.get("bones")
        root = bones.get("root") if isinstance(bones, dict) else None
        if not isinstance(root, dict):
            return None

        source = self._fit_image(source)
        width, height = source.size
        try:
            scale_x = max(0.5, min(1.6, float(root.get("scale_x", 1.0))))
            scale_y = max(0.5, min(1.6, float(root.get("scale_y", 1.0))))
            rotation = float(root.get("rotation", 0.0))
            y_offset = round(float(root.get("y", 0.0)))
        except (TypeError, ValueError):
            return None
        resized = source.resize(
            (max(1, round(width * scale_x)), max(1, round(height * scale_y))),
            self.Image.Resampling.LANCZOS,
        )
        rotated = resized.rotate(
            rotation,
            resample=self.Image.Resampling.BICUBIC,
            expand=False,
        )
        canvas = self.Image.new("RGBA", (width, height), (0, 0, 0, 0))
        self._paste_center(canvas, rotated, y_offset)
        return canvas

    def _frame_count(self, role: str, source_count: int) -> int:
        if source_count > 1:
            return source_count
        defaults = {"idle": 8, "walk": 12, "sleep": 10, "react": 6}
        sequences = self.config.animation.get("sequences", {})
        sequence = sequences.get(role, {}) if isinstance(sequences, dict) else {}
        if isinstance(sequence, dict):
            configured = sequence.get("fallback_frame_count")
            if configured:
                try:
                    return max(1, min(12, int(configured)))
                except (TypeError, ValueError):
                    pass
        return defaults.get(role, 8)

    def _load_animation_frames(self) -> Dict[str, List[Any]]:
        sources = self._source_images()
        frames: Dict[str, List[Any]] = {}
        for role in ("idle", "walk", "sleep", "react"):
            role_sources = sources[role]
            frame_count = self._frame_count(role, len(role_sources))
            rendered = []
            for index in range(frame_count):
                source = role_sources[index % len(role_sources)]
                if self.animation_mode in {"skeleton", "hybrid"} and self.skeleton_manifest:
                    skeleton_frame = self._skeleton_frame(source, role, index)
                else:
                    skeleton_frame = None
                if skeleton_frame is not None and self.animation_mode == "skeleton":
                    image = skeleton_frame
                elif len(role_sources) > 1:
                    image = self._fit_image(source)
                else:
                    image = self._animation_frame(source, role, index)
                rendered.append(self.ImageTk.PhotoImage(image))
            frames[role] = rendered
        return frames

    def _build_menu(self):
        import tkinter as tk

        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="现在睡觉", command=lambda: self._enter_state("sleep", 8.0))
        self.menu.add_command(label="恢复活动", command=lambda: self._enter_state("idle", 0.0))
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.close)

    def _set_geometry(self, width: int, height: int):
        self.root.geometry(f"{width}x{height}+{round(self.x)}+{round(self.y)}")

    def _enter_state(self, state: str, duration: float):
        self.state = state
        self.state_started = time.monotonic()
        self.state_duration = duration
        self.frame_index = 0
        self.next_frame_at = 0.0

    def _on_press(self, event):
        self.drag_start = (event.x_root, event.y_root, round(self.x), round(self.y))
        self.dragged = False

    def _on_motion(self, event):
        if not self.drag_start:
            return
        start_x, start_y, origin_x, origin_y = self.drag_start
        delta_x = event.x_root - start_x
        delta_y = event.y_root - start_y
        if abs(delta_x) + abs(delta_y) > 4:
            self.dragged = True
        self.x = origin_x + delta_x
        self.y = origin_y + delta_y
        self._set_geometry(self.canvas.winfo_width(), self.canvas.winfo_height())

    def _on_release(self, _event):
        if not self.dragged:
            self.last_interaction = time.monotonic()
            self._enter_state("react", 1.2)
        self.drag_start = None

    def _show_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    def _maybe_change_state(self, now: float):
        elapsed = now - self.state_started
        if self.state == "react" and elapsed >= self.state_duration:
            self._enter_state("idle", 0.0)
        elif self.state == "sleep" and elapsed >= self.state_duration:
            self._enter_state("idle", 0.0)
        elif self.state == "idle":
            if now - self.last_interaction >= self.config.sleep_after_seconds:
                self._enter_state("sleep", random.uniform(8.0, 16.0))
            elif random.random() < 0.012:
                self.direction = random.choice((-1, 1))
                self._enter_state("walk", random.uniform(3.0, 8.0))
        elif self.state == "walk" and elapsed >= self.state_duration:
            self._enter_state("idle", 0.0)

    def _move(self):
        if self.state != "walk":
            return
        screen_width = self.root.winfo_screenwidth()
        width = self.canvas.winfo_width()
        self.x += self.direction * self.config.speed
        if self.x <= 0:
            self.x = 0
            self.direction = 1
        elif self.x + width >= screen_width:
            self.x = max(0, screen_width - width)
            self.direction = -1
        self._set_geometry(width, self.canvas.winfo_height())

    def _tick(self):
        now = time.monotonic()
        self._maybe_change_state(now)
        self._move()
        role_frames = self.frames[self.state]
        frame = role_frames[self.frame_index % len(role_frames)]
        self.photo_image = frame
        if self.image_item is None:
            self.image_item = self.canvas.create_image(
                0,
                0,
                anchor="nw",
                image=frame,
                tags="pet",
            )
        else:
            self.canvas.itemconfig(self.image_item, image=frame)
        if self.next_frame_at <= 0.0:
            self.next_frame_at = now + self.frame_interval
        elif now >= self.next_frame_at:
            elapsed = now - self.next_frame_at
            steps = 1 + int(elapsed / self.frame_interval)
            self.frame_index += steps
            self.next_frame_at += steps * self.frame_interval
        self.root.after(self.tick_ms, self._tick)

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self._tick()
        self.root.mainloop()


def run_pet(config_path: Optional[Union[str, Path]] = None) -> None:
    try:
        config, base_dir = load_bundle(config_path)
        PetWindow(config, base_dir).run()
    except Exception as error:
        # A generated --noconsole executable cannot show a traceback. A small
        # native dialog gives the user an actionable error instead.
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("桌面宠物启动失败", str(error))
            root.destroy()
        except Exception:
            print(error, file=sys.stderr)
        raise


def self_test_runtime() -> None:
    """Import the bundled Pillow extensions without creating a GUI window."""

    from PIL import Image, ImageEnhance, ImageTk  # noqa: F401
    import PIL._imaging  # noqa: F401


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a generated desktop pet")
    parser.add_argument("--config", help="path to pet_config.json for preview")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="verify bundled Pillow extensions without starting the desktop window",
    )
    args = parser.parse_args()
    if args.self_test:
        self_test_runtime()
        return
    run_pet(args.config)


if __name__ == "__main__":
    main()

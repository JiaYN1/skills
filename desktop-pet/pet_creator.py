"""Photo-to-desktop-pet generator for Windows.

Run this file on Windows, select one to several photos, and it creates a
portable package. If PyInstaller is installed, it can also build a single
``.exe`` containing the normalized PNG assets and runtime.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from pet_assets import normalize_pet_image
from pet_animation import normalize_animation_mode, write_animation_bundle
from pet_common import IMAGE_EXTENSIONS, ROLE_LABELS, ROLES, assign_roles, safe_filename, unique_directory


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = APP_ROOT / "generated-pets"


def _as_path_list(values: Sequence[str]) -> List[Path]:
    return [Path(value) for value in values if Path(value).suffix.lower() in IMAGE_EXTENSIONS]


def create_package(
    photo_paths: List[Path],
    pet_name: str,
    output_parent: Path,
    use_rembg: bool,
    animation_mode: str = "hybrid",
) -> Path:
    """Create a self-contained, previewable pet package."""

    output_parent.mkdir(parents=True, exist_ok=True)
    package_dir = unique_directory(output_parent, pet_name)
    assets_dir = package_dir / "assets"
    assets_dir.mkdir(parents=True)

    roles = assign_roles(photo_paths)
    config_assets: Dict[str, List[str]] = {}
    for role in ROLES:
        config_assets[role] = []
        for index, source_path in enumerate(roles[role]):
            filename = f"{role}_{index}.png"
            destination = assets_dir / filename
            normalize_pet_image(
                source_path,
                destination,
                canvas_size=320,
                use_rembg=use_rembg,
                background_mode="auto" if use_rembg else "simple",
                anchor="center" if role == "sleep" else "bottom",
                subject_scale=0.96,
            )
            config_assets[role].append(f"assets/{filename}")

    animation_manifest = write_animation_bundle(
        package_dir,
        config_assets,
        mode=normalize_animation_mode(animation_mode),
        fps=10,
    )

    config = {
        "name": pet_name.strip() or "我的宠物",
        "scale": 1.0,
        "speed": 2.2,
        "always_on_top": True,
        "sleep_after_seconds": 60,
        "assets": config_assets,
        "animation": animation_manifest,
    }
    (package_dir / "pet_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    shutil.copy2(APP_ROOT / "pet_runtime.py", package_dir / "pet_runtime.py")
    (package_dir / "README.txt").write_text(
        "这是一个可预览的桌面宠物资源包。\n"
        "运行预览：python pet_runtime.py --config pet_config.json\n"
        "动作分配：第 1 张待机，第 2 张走动，第 3 张睡觉，第 4 张点击反应；更多照片会作为走动备用帧。\n"
        "animation.json 保存逐帧信息；hybrid/skeleton 模式还包含 skeleton.json。\n",
        encoding="utf-8",
    )
    return package_dir


def build_exe(package_dir: Path, pet_name: str) -> Path:
    """Build a one-file executable using the local Windows Python toolchain."""

    safe_name = safe_filename(pet_name, "my-pet").replace(" ", "_")
    dist_dir = package_dir / "dist"
    work_dir = package_dir / "build"
    spec_dir = package_dir / "spec"
    separator = ";" if os.name == "nt" else ":"
    config_data = f"{package_dir / 'pet_config.json'}{separator}."
    assets_data = f"{package_dir / 'assets'}{separator}assets"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name",
        safe_name,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--add-data",
        config_data,
        "--add-data",
        assets_data,
    ]
    for metadata_name in ("animation.json", "skeleton.json"):
        metadata_path = package_dir / metadata_name
        if metadata_path.exists():
            command.extend(["--add-data", f"{metadata_path}{separator}."])
    command.append(str(APP_ROOT / "pet_runtime.py"))
    result = subprocess.run(
        command,
        cwd=APP_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"PyInstaller 打包失败：\n{details[-3000:]}")

    executable = dist_dir / (f"{safe_name}.exe" if os.name == "nt" else safe_name)
    if not executable.exists():
        raise RuntimeError("PyInstaller 没有生成预期的可执行文件")
    return executable


class CreatorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("宠物桌面生成器")
        self.geometry("720x560")
        self.minsize(620, 480)
        self.photo_paths: List[Path] = []
        self.name_var = tk.StringVar(value="我的宠物")
        self.output_var = tk.StringVar(value=str(DEFAULT_OUTPUT))
        self.use_rembg_var = tk.BooleanVar(value=False)
        self.build_exe_var = tk.BooleanVar(value=True)
        self.animation_mode_var = tk.StringVar(value="hybrid")
        self.status_var = tk.StringVar(value="请选择 1-8 张照片。第一张作为待机动作。")
        self._build_ui()

    def _build_ui(self):
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="宠物桌面生成器", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(
            root,
            text="把宠物照片转换成可以在 Windows 桌面上活动的小宠物。",
        ).pack(anchor="w", pady=(4, 16))

        info = ttk.LabelFrame(root, text="基本信息", padding=12)
        info.pack(fill="x")
        ttk.Label(info, text="宠物名称").grid(row=0, column=0, sticky="w")
        ttk.Entry(info, textvariable=self.name_var, width=32).grid(row=0, column=1, sticky="ew", padx=(10, 0))
        info.columnconfigure(1, weight=1)

        photos = ttk.LabelFrame(root, text="照片（按顺序分配动作）", padding=12)
        photos.pack(fill="both", expand=True, pady=12)

        self.photo_list = tk.Listbox(photos, height=9, activestyle="none")
        self.photo_list.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(photos, orient="vertical", command=self.photo_list.yview)
        scrollbar.pack(side="left", fill="y")
        self.photo_list.configure(yscrollcommand=scrollbar.set)

        controls = ttk.Frame(photos)
        controls.pack(side="left", fill="y", padx=(12, 0))
        ttk.Button(controls, text="添加照片", command=self._add_photos).pack(fill="x")
        ttk.Button(controls, text="移除选中", command=self._remove_photo).pack(fill="x", pady=6)
        ttk.Button(controls, text="上移", command=lambda: self._move_photo(-1)).pack(fill="x")
        ttk.Button(controls, text="下移", command=lambda: self._move_photo(1)).pack(fill="x", pady=6)
        ttk.Label(
            controls,
            text="1 待机\n2 走动\n3 睡觉\n4 点击反应\n5+ 走动备用帧",
            justify="left",
        ).pack(anchor="w", pady=(16, 0))

        output = ttk.LabelFrame(root, text="输出", padding=12)
        output.pack(fill="x")
        ttk.Label(output, text="输出目录").grid(row=0, column=0, sticky="w")
        ttk.Entry(output, textvariable=self.output_var).grid(row=0, column=1, sticky="ew", padx=10)
        ttk.Button(output, text="选择", command=self._choose_output).grid(row=0, column=2)
        output.columnconfigure(1, weight=1)

        options = ttk.Frame(root)
        options.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(
            options,
            text="尝试自动抠图（需要额外安装 rembg，失败时自动使用轻量抠图）",
            variable=self.use_rembg_var,
        ).pack(anchor="w")
        ttk.Checkbutton(
            options,
            text="同时生成 Windows exe（需要在 Windows 环境安装 PyInstaller）",
            variable=self.build_exe_var,
        ).pack(anchor="w", pady=(5, 0))
        animation_options = ttk.Frame(options)
        animation_options.pack(fill="x", pady=(10, 0))
        ttk.Label(animation_options, text="动画输出").pack(side="left")
        ttk.Combobox(
            animation_options,
            textvariable=self.animation_mode_var,
            values=("hybrid", "png", "skeleton"),
            state="readonly",
            width=12,
        ).pack(side="left", padx=(10, 0))
        ttk.Label(
            animation_options,
            text="hybrid 同时保存逐帧 PNG 与 2D 骨骼清单",
        ).pack(side="left", padx=(10, 0))

        footer = ttk.Frame(root)
        footer.pack(fill="x", pady=(16, 0))
        ttk.Label(footer, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
        self.generate_button = ttk.Button(footer, text="生成宠物", command=self._generate)
        self.generate_button.pack(side="right")

    def _add_photos(self):
        paths = filedialog.askopenfilenames(
            title="选择宠物照片",
            filetypes=[
                ("图片", " ".join(f"*{extension}" for extension in sorted(IMAGE_EXTENSIONS))),
                ("所有文件", "*.*"),
            ],
        )
        for path in _as_path_list(paths):
            if path not in self.photo_paths and len(self.photo_paths) < 8:
                self.photo_paths.append(path)
        self._refresh_photo_list()

    def _refresh_photo_list(self):
        self.photo_list.delete(0, tk.END)
        for index, path in enumerate(self.photo_paths, start=1):
            role = ROLE_LABELS[ROLES[min(index - 1, 3)]] if index <= 4 else "走动备用帧"
            self.photo_list.insert(tk.END, f"{index}. [{role}] {path.name}")

    def _remove_photo(self):
        selection = self.photo_list.curselection()
        if selection:
            del self.photo_paths[selection[0]]
            self._refresh_photo_list()

    def _move_photo(self, direction: int):
        selection = self.photo_list.curselection()
        if not selection:
            return
        old_index = selection[0]
        new_index = old_index + direction
        if 0 <= new_index < len(self.photo_paths):
            self.photo_paths[old_index], self.photo_paths[new_index] = (
                self.photo_paths[new_index],
                self.photo_paths[old_index],
            )
            self._refresh_photo_list()
            self.photo_list.selection_set(new_index)

    def _choose_output(self):
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_var.set(path)

    def _set_busy(self, busy: bool):
        self.generate_button.configure(state="disabled" if busy else "normal")

    def _generate(self):
        if not self.photo_paths:
            messagebox.showwarning("还没有照片", "请至少添加一张宠物照片。")
            return
        if not self.name_var.get().strip():
            messagebox.showwarning("缺少名称", "请填写宠物名称。")
            return

        self._set_busy(True)
        self.status_var.set("正在处理照片，请稍候……")
        arguments = (
            list(self.photo_paths),
            self.name_var.get().strip(),
            Path(self.output_var.get()).expanduser(),
            self.use_rembg_var.get(),
            self.build_exe_var.get(),
            self.animation_mode_var.get(),
        )
        threading.Thread(target=self._generate_worker, args=(arguments,), daemon=True).start()

    def _generate_worker(self, arguments):
        photos, name, output, use_rembg, should_build, animation_mode = arguments
        try:
            package_dir = create_package(photos, name, output, use_rembg, animation_mode)
            executable = None
            if should_build:
                self.after(0, lambda: self.status_var.set("资源已生成，正在调用 PyInstaller 打包……"))
                executable = build_exe(package_dir, name)
            self.after(0, lambda: self._generation_done(package_dir, executable))
        except Exception as error:
            self.after(0, lambda: self._generation_failed(error))

    def _generation_done(self, package_dir: Path, executable: Optional[Path]):
        self._set_busy(False)
        if executable:
            self.status_var.set(f"完成：{executable}")
            messagebox.showinfo("生成完成", f"Windows exe 已生成：\n{executable}")
        else:
            self.status_var.set(f"资源包已生成：{package_dir}")
            messagebox.showinfo("生成完成", f"资源包已生成：\n{package_dir}")

    def _generation_failed(self, error: Exception):
        self._set_busy(False)
        self.status_var.set("生成失败，请查看错误提示。")
        messagebox.showerror("生成失败", str(error))


def main():
    CreatorApp().mainloop()


if __name__ == "__main__":
    main()

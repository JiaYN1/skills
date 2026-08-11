"""Windows worker that turns a generated resource zip into a Windows exe."""

from __future__ import annotations

import json
import argparse
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import requests


SERVER_URL = os.getenv("PET_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")
POLL_SECONDS = max(2, int(os.getenv("POLL_SECONDS", "5")))
PYINSTALLER_PIL_OPTIONS = [
    "--collect-binaries",
    "PIL",
    "--hidden-import",
    "PIL.Image",
    "--hidden-import",
    "PIL.ImageEnhance",
    "--hidden-import",
    "PIL.ImageTk",
    "--hidden-import",
    "PIL._imaging",
    "--hidden-import",
    "PIL._imagingtk",
]


class WorkerCancelled(RuntimeError):
    """Raised when the server asks the Windows build to stop."""


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Windows desktop-pet build worker")
    parser.add_argument("--check", action="store_true", help="check server and worker authentication, then exit")
    args = parser.parse_args()

    if not WORKER_TOKEN:
        raise SystemExit("请设置 WORKER_TOKEN")
    if args.check:
        check_connection()
        return
    print(f"Windows Worker 已启动：{SERVER_URL}")
    while True:
        try:
            job = claim_job()
        except Exception as error:
            print(f"连接服务器失败：{error}")
            time.sleep(POLL_SECONDS)
            continue
        if not job:
            time.sleep(POLL_SECONDS)
            continue
        try:
            process_job(job)
        except Exception as error:
            print(f"任务 {job['id']} 失败：{error}")
            post_error(job["id"], str(error))


def claim_job():
    response = requests.get(
        f"{SERVER_URL}/api/worker/jobs/next",
        headers=headers(),
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("job")


def check_connection() -> None:
    health = requests.get(f"{SERVER_URL}/healthz", timeout=30)
    health.raise_for_status()
    worker = requests.get(
        f"{SERVER_URL}/api/worker/healthz",
        headers=headers(),
        timeout=30,
    )
    worker.raise_for_status()
    print(f"服务器连接正常：{health.json()}")
    print("Worker 鉴权正常")


def process_job(job) -> None:
    with tempfile.TemporaryDirectory(prefix="desktop-pet-") as temporary:
        root = Path(temporary)
        source_zip = root / "source.zip"
        response = requests.get(
            f"{SERVER_URL}{job['source_url']}",
            headers=headers(),
            timeout=120,
        )
        response.raise_for_status()
        source_zip.write_bytes(response.content)
        if is_cancelled(job):
            print(f"任务 {job['id']} 已被停止，跳过打包")
            return
        post_progress(job, 92, "Worker 已下载资源包，开始构建 exe")
        extract_zip(source_zip, root / "package")
        package = root / "package"
        config = json.loads((package / "pet_config.json").read_text(encoding="utf-8"))
        executable = build_exe(
            package,
            config.get("name", job["name"]),
            should_cancel=lambda: is_cancelled(job),
        )
        if is_cancelled(job):
            print(f"任务 {job['id']} 在构建期间被停止，忽略 exe")
            return
        post_progress(job, 99, "exe 已构建完成，正在上传")
        with executable.open("rb") as handle:
            upload = {"artifact": (executable.name, handle, "application/vnd.microsoft.portable-executable")}
            result = requests.post(
                f"{SERVER_URL}{job['artifact_url']}",
                headers=headers(),
                files=upload,
                timeout=180,
            )
        result.raise_for_status()
        print(f"任务 {job['id']} 完成：{executable.name}")


def is_cancelled(job) -> bool:
    status_url = job.get("status_url")
    if not status_url:
        return False
    response = requests.get(
        f"{SERVER_URL}{status_url}",
        headers=headers(),
        timeout=30,
    )
    response.raise_for_status()
    return bool(response.json().get("cancel_requested"))


def post_progress(job, progress: int, message: str) -> None:
    progress_url = job.get("progress_url")
    if not progress_url:
        return
    response = requests.post(
        f"{SERVER_URL}{progress_url}",
        headers=headers(),
        json={"progress": progress, "message": message},
        timeout=30,
    )
    response.raise_for_status()
    if response.json().get("cancelled"):
        raise RuntimeError("任务已停止")


def build_exe(package: Path, pet_name: str, should_cancel=None) -> Path:
    safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in pet_name).strip(" .") or "my-pet"
    dist = package / "dist"
    spec = package / "spec"
    configured_cache = os.getenv("PET_PYINSTALLER_CACHE", "").strip()
    work = (Path(configured_cache) / safe_name) if configured_cache else package / "build"
    work.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--noconsole",
        *PYINSTALLER_PIL_OPTIONS,
        "--name",
        safe_name,
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(spec),
        "--add-data",
        f"{package / 'pet_config.json'};.",
        "--add-data",
        f"{package / 'assets'};assets",
    ]
    if _env_bool("PYINSTALLER_CLEAN"):
        command.insert(4, "--clean")
    for metadata_name in ("animation.json", "skeleton.json"):
        metadata_path = package / metadata_name
        if metadata_path.exists():
            command.extend(["--add-data", f"{metadata_path};."])
    command.append(str(package / "pet_runtime.py"))
    result = _run_pyinstaller(command, package, should_cancel)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"PyInstaller 打包失败：\n{details[-3000:]}")
    executable = dist / f"{safe_name}.exe"
    if not executable.exists():
        raise RuntimeError("PyInstaller 没有生成 exe")
    self_test = subprocess.run(
        [str(executable), "--self-test"],
        cwd=str(package),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if self_test.returncode != 0:
        details = (self_test.stderr or self_test.stdout).strip()
        raise RuntimeError(
            "生成的 exe 自检失败，Pillow 原生扩展可能未被打包：\n"
            f"{details[-3000:]}"
        )
    return executable


def _run_pyinstaller(command, package: Path, should_cancel=None):
    """Run PyInstaller while allowing the server stop request to terminate it."""

    if should_cancel is None:
        return subprocess.run(command, cwd=str(package), check=False)

    process = subprocess.Popen(
        command,
        cwd=str(package),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout = ""
    stderr = ""
    while True:
        try:
            if should_cancel():
                _terminate_process(process)
                raise WorkerCancelled("任务已停止，已终止 Windows exe 打包")
        except WorkerCancelled:
            raise
        except Exception:
            # A transient status request failure should not orphan the
            # PyInstaller process or turn a healthy build into a cancellation.
            pass
        try:
            stdout, stderr = process.communicate(timeout=1)
            break
        except subprocess.TimeoutExpired:
            continue
    return SimpleNamespace(
        returncode=process.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
    )


def _terminate_process(process) -> None:
    """Terminate a cancellable build and force-kill it if needed."""

    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()


def extract_zip(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(source) as archive:
        for item in archive.infolist():
            target = (destination / item.filename).resolve()
            if destination_resolved not in target.parents and target != destination_resolved:
                raise RuntimeError("资源包包含非法路径")
        archive.extractall(destination)


def post_error(job_id: str, message: str) -> None:
    try:
        requests.post(
            f"{SERVER_URL}/api/worker/jobs/{job_id}/error",
            headers=headers(),
            json={"message": message[-4000:]},
            timeout=30,
        )
    except Exception as error:
        print(f"无法回报失败任务：{error}")


def headers():
    return {"X-Worker-Token": WORKER_TOKEN}


if __name__ == "__main__":
    main()

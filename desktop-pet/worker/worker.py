"""Windows worker that turns a generated resource zip into a Windows exe."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import requests


SERVER_URL = os.getenv("PET_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")
POLL_SECONDS = max(2, int(os.getenv("POLL_SECONDS", "5")))


def main() -> None:
    if not WORKER_TOKEN:
        raise SystemExit("请设置 WORKER_TOKEN")
    print(f"Windows Worker 已启动：{SERVER_URL}")
    while True:
        job = claim_job()
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
        extract_zip(source_zip, root / "package")
        package = root / "package"
        config = json.loads((package / "pet_config.json").read_text(encoding="utf-8"))
        executable = build_exe(package, config.get("name", job["name"]))
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


def build_exe(package: Path, pet_name: str) -> Path:
    safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in pet_name).strip(" .") or "my-pet"
    dist = package / "dist"
    work = package / "build"
    spec = package / "spec"
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
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(spec),
        "--add-data",
        f"{package / 'pet_config.json'};.",
        "--add-data",
        f"{package / 'assets'};assets",
        str(package / "pet_runtime.py"),
    ]
    subprocess.run(command, cwd=str(package), check=True)
    executable = dist / f"{safe_name}.exe"
    if not executable.exists():
        raise RuntimeError("PyInstaller 没有生成 exe")
    return executable


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


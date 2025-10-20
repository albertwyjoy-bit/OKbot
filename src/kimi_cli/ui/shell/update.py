import os
import platform
import re
import shutil
import stat
import tarfile
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

from kimi_cli.ui.shell.console import console
from kimi_cli.ui.shell.metacmd import meta_command
from kimi_cli.utils.logging import logger

if TYPE_CHECKING:
    from kimi_cli.ui.shell import ShellApp


BASE_URL = "https://cdn.kimi.com/binaries/kimi-cli"
LATEST_VERSION_URL = f"{BASE_URL}/latest"
INSTALL_DIR = Path.home() / ".local" / "bin"


@meta_command
async def update(app: "ShellApp", args: list[str]):
    """Check for updates"""
    from kimi_cli import __version__ as current_version

    def semver_tuple(v: str) -> tuple[int, int, int]:
        v = v.strip()
        if v.startswith("v"):
            v = v[1:]
        m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", v)
        if not m:
            return (0, 0, 0)
        major = int(m.group(1))
        minor = int(m.group(2))
        patch = int(m.group(3) or 0)
        return (major, minor, patch)

    def detect_target() -> str | None:
        sys_name = platform.system()
        mach = platform.machine()
        if mach in ("x86_64", "amd64", "AMD64"):
            arch = "x86_64"
        elif mach in ("arm64", "aarch64"):
            arch = "aarch64"
        else:
            logger.error("Unsupported architecture: {mach}", mach=mach)
            return None
        if sys_name == "Darwin":
            os_name = "apple-darwin"
        elif sys_name == "Linux":
            os_name = "unknown-linux-gnu"
        else:
            logger.error("Unsupported OS: {sys_name}", sys_name=sys_name)
            return None
        return f"{arch}-{os_name}"

    async def get_latest_version(session: aiohttp.ClientSession) -> str | None:
        try:
            async with session.get(LATEST_VERSION_URL) as resp:
                resp.raise_for_status()
                data = await resp.text()
                return data.strip()
        except aiohttp.ClientError:
            logger.exception("Failed to get latest version:")
            return None

    target = detect_target()
    if not target:
        logger.error("Failed to detect target platform.")
        console.print("[bold red]Failed to detect target platform.[/bold red]")
        return

    logger.info("Checking for updates...")
    console.print("[bold]Checking for updates...[/bold]")
    async with aiohttp.ClientSession() as session:
        latest_version = await get_latest_version(session)
        if not latest_version:
            logger.error("Failed to check for updates.")
            console.print("[bold red]Failed to check for updates.[/bold red]")
            return
        logger.info("Latest version: {latest_version}", latest_version=latest_version)

    cur_t = semver_tuple(current_version)
    lat_t = semver_tuple(latest_version)

    if cur_t >= lat_t:
        console.print("[bold green]Already up to date.[/bold green]")
        return

    logger.info(
        "Updating from {current_version} to {latest_version}...",
        current_version=current_version,
        latest_version=latest_version,
    )
    console.print(f"[bold]Updating from {current_version} to {latest_version}...[/bold]")

    filename = f"kimi-{latest_version}-{target}.tar.gz"
    download_url = f"{BASE_URL}/{latest_version}/{filename}"

    # Prepare temp dir and paths
    with tempfile.TemporaryDirectory(prefix="kimi-update-") as tmpdir:
        tar_path = os.path.join(tmpdir, filename)

        logger.info("Downloading from {download_url}...", download_url=download_url)
        console.print("[grey50]Downloading...[/grey50]")
        try:
            async with aiohttp.ClientSession() as session, session.get(download_url) as resp:
                resp.raise_for_status()
                with open(tar_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 64):
                        if chunk:
                            f.write(chunk)
        except Exception:
            logger.exception("Failed to download.")
            console.print("[bold red]Failed to download.[/bold red]")
            return

        # TODO: check SHA256 checksum

        logger.info("Extracting archive {tar_path}...", tar_path=tar_path)
        console.print("[grey50]Extracting...[/grey50]")
        try:
            with tarfile.open(tar_path, "r:gz") as tar:
                tar.extractall(tmpdir)
            # find 'kimi' file
            binary_path = None
            for root, _, files in os.walk(tmpdir):
                if "kimi" in files:
                    binary_path = os.path.join(root, "kimi")
                    break
            if not binary_path:
                logger.error("Binary 'kimi' not found in archive.")
                console.print("[bold red]Binary 'kimi' not found in archive.[/bold red]")
                return
        except Exception:
            logger.exception("Failed to extract archive.")
            console.print("[bold red]Failed to extract archive.[/bold red]")
            return

        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        dest_path = INSTALL_DIR / "kimi"
        logger.info("Installing to {dest_path}...", dest_path=dest_path)
        console.print("[grey50]Installing...[/grey50]")

        try:
            shutil.copy2(binary_path, dest_path)
            os.chmod(
                dest_path, os.stat(dest_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )
        except Exception:
            logger.exception("Failed to install.")
            console.print("[bold red]Failed to install.[/bold red]")
            return

    console.print("[bold green]Updated successfully![/bold green]")
    console.print("[bold yellow]Restart Kimi CLI to use the new version.[/bold yellow]")

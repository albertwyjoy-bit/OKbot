from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
DIST_DIR = WEB_DIR / "dist"
NODE_MODULES = WEB_DIR / "node_modules"
STATIC_DIR = ROOT / "src" / "kimi_cli" / "web" / "static"
PNPM_LOCK = WEB_DIR / "pnpm-lock.yaml"

STRICT_VERSION = os.environ.get("KIMI_WEB_STRICT_VERSION", "").lower() in {"1", "true", "yes"}


def read_pyproject_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    return str(data["project"]["version"])


def find_version_in_dist(version: str) -> bool:
    search_suffixes = {".js", ".css", ".html", ".map"}
    version_with_prefix = f"v{version}"
    found_plain = False

    for path in DIST_DIR.rglob("*"):
        if not path.is_file() or path.suffix not in search_suffixes:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if version_with_prefix in content:
            return True
        if version in content:
            found_plain = True

    return found_plain


def resolve_package_manager() -> tuple[str, str] | None:
    """Resolve package manager (pnpm or npm).
    
    Returns:
        Tuple of (command, install_subcommand) or None if not found.
        install_subcommand is 'install' for pnpm or 'ci' for npm.
    """
    # Check for pnpm if pnpm-lock.yaml exists
    if PNPM_LOCK.exists():
        pnpm = shutil.which("pnpm")
        if pnpm:
            return (pnpm, "install")
        print(
            "Warning: pnpm-lock.yaml found but pnpm not available. "
            "Falling back to npm.",
            file=sys.stderr,
        )
    
    # Fall back to npm
    candidates = ["npm"]
    if os.name == "nt":
        candidates.extend(["npm.cmd", "npm.exe", "npm.bat"])
    for candidate in candidates:
        npm = shutil.which(candidate)
        if npm:
            return (npm, "ci")
    return None


def run_npm(cmd: str, args: list[str]) -> int:
    try:
        result = subprocess.run([cmd, *args], check=False)
    except FileNotFoundError:
        print(
            f"{cmd} not found or failed to execute. Install Node.js and ensure it is on PATH.",
            file=sys.stderr,
        )
        return 1
    return result.returncode


def main() -> int:
    pkg_manager_result = resolve_package_manager()
    if pkg_manager_result is None:
        print("npm/pnpm not found. Install Node.js to build the web UI.", file=sys.stderr)
        return 1
    
    pkg_manager, install_cmd = pkg_manager_result

    expected_version = read_pyproject_version()
    explicit_expected = os.environ.get("KIMI_WEB_EXPECT_VERSION")
    if explicit_expected and explicit_expected != expected_version:
        print(
            f"web version mismatch: pyproject={expected_version}, expected={explicit_expected}",
            file=sys.stderr,
        )
        return 1

    if not NODE_MODULES.exists():
        print(f"Installing dependencies with {pkg_manager}...")
        returncode = run_npm(pkg_manager, ["--prefix", str(WEB_DIR), install_cmd])
        if returncode != 0:
            return returncode

    print("Building web UI...")
    returncode = run_npm(pkg_manager, ["--prefix", str(WEB_DIR), "run", "build"])
    if returncode != 0:
        return returncode

    if not DIST_DIR.exists():
        print("web/dist not found after build. Check the web build output.", file=sys.stderr)
        return 1
    if STRICT_VERSION and not find_version_in_dist(expected_version):
        print(
            f"web version not found in build output; expected version {expected_version}",
            file=sys.stderr,
        )
        return 1

    if STATIC_DIR.exists():
        shutil.rmtree(STATIC_DIR)
    STATIC_DIR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DIST_DIR, STATIC_DIR)

    print(f"Synced web UI to {STATIC_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

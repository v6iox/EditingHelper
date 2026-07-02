"""Self-update support.

Checks the GitHub Releases API for a newer version and can download and
install it in place:

- macOS: mounts the new DMG, swaps the .app bundle, relaunches
- Windows: extracts the new zip and hands off to a script that swaps the
  install folder after the app exits, then relaunches

For private repositories the API needs a token; the updater looks in the
EDITSYNC_GITHUB_TOKEN environment variable and the app settings. Without
access it stays silent — the app just never offers updates.
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import __version__

REPO = "v6iox/EditingHelper"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
MAC_ASSET = "EditSync.dmg"
WIN_ASSET = "EditSync-windows.zip"
TIMEOUT = 10


class UpdateError(RuntimeError):
    pass


@dataclass
class UpdateInfo:
    version: str  # e.g. "1.1.0"
    tag: str  # e.g. "v1.1.0"
    asset_url: str  # API url of the platform asset
    page_url: str  # human release page


@dataclass
class CheckResult:
    """Outcome of an update check, suitable for showing to the user.

    status: "update" (info is set), "current" (detail = newest version),
    or "error" (detail = a plain-language reason)."""

    status: str
    info: Optional[UpdateInfo] = None
    detail: str = ""


def _ssl_context() -> ssl.SSLContext:
    """A context that can actually verify github.com inside the app.

    The packaged app (PyInstaller) ships Python's ssl module with NO
    certificate store — macOS never exposes its keychain to OpenSSL —
    so a bare urlopen() fails certificate verification on every machine
    and the updater silently did nothing. certifi bundles the CAs; fall
    back to the system default outside the packaged app."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3); non-numeric parts terminate the tuple."""
    parts: list[int] = []
    for piece in text.strip().lstrip("vV").split("."):
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(candidate: str, current: str = __version__) -> bool:
    cand, cur = parse_version(candidate), parse_version(current)
    return bool(cand) and cand > cur


def _token() -> Optional[str]:
    token = os.environ.get("EDITSYNC_GITHUB_TOKEN")
    if token:
        return token
    try:  # optional: a token stored by the app settings
        from PySide6.QtCore import QSettings

        stored = QSettings("86 Auto Lab", "EditSync").value("github_token")
        return str(stored) if stored else None
    except Exception:
        return None


def _request(url: str, accept: str) -> urllib.request.Request:
    headers = {"User-Agent": "EditSync-updater", "Accept": accept}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def platform_asset_name() -> str:
    return WIN_ASSET if sys.platform == "win32" else MAC_ASSET


def check_for_update_detailed() -> CheckResult:
    """Ask GitHub for the latest release and say what happened.

    Never raises — every failure becomes a CheckResult("error", ...)
    with a reason a person can act on. The silent auto-check at launch
    and the visible "Check for updates" button both run through here."""
    try:
        with urllib.request.urlopen(
            _request(API_LATEST, "application/vnd.github+json"),
            timeout=TIMEOUT,
            context=_ssl_context(),
        ) as response:
            release = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return CheckResult("error", detail="No releases were found.")
        if exc.code in (403, 429):
            return CheckResult(
                "error",
                detail="GitHub is rate-limiting this network — "
                "try again in a few minutes.",
            )
        return CheckResult(
            "error", detail=f"GitHub answered with an error (HTTP {exc.code})."
        )
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLError) or "certificate" in str(reason).lower():
            return CheckResult(
                "error",
                detail="Secure connection to GitHub failed — the server's "
                "certificate couldn't be verified.",
            )
        return CheckResult(
            "error", detail="Couldn't reach GitHub — check your internet connection."
        )
    except (TimeoutError, ValueError, OSError):
        return CheckResult(
            "error", detail="Couldn't reach GitHub — check your internet connection."
        )

    tag = release.get("tag_name", "")
    if not is_newer(tag):
        return CheckResult("current", detail=tag.lstrip("vV") or __version__)
    wanted = platform_asset_name()
    asset = next(
        (a for a in release.get("assets", []) if a.get("name") == wanted),
        None,
    )
    if asset is None:
        return CheckResult(
            "error",
            detail=f"{tag} is out, but its download isn't ready yet — "
            "try again in a few minutes.",
        )
    return CheckResult(
        "update",
        info=UpdateInfo(
            version=tag.lstrip("vV"),
            tag=tag,
            asset_url=asset.get("url", ""),
            page_url=release.get("html_url", RELEASES_PAGE),
        ),
    )


def check_for_update() -> Optional[UpdateInfo]:
    """Return UpdateInfo when a newer release exists, else None."""
    return check_for_update_detailed().info


def download_asset(info: UpdateInfo, progress=lambda pct: None) -> Path:
    """Download the platform asset to a temp file, reporting 0-100."""
    target = Path(tempfile.mkdtemp(prefix="editsync-update-")) / platform_asset_name()
    try:
        with urllib.request.urlopen(
            _request(info.asset_url, "application/octet-stream"),
            timeout=TIMEOUT,
            context=_ssl_context(),
        ) as response, open(target, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total:
                    progress(min(99, int(done * 100 / total)))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Download failed: {exc}") from exc
    progress(100)
    return target


def current_app_root() -> Optional[Path]:
    """The installed app to replace, or None when not running frozen."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent
        return None
    return exe.parent


def install_and_restart(payload: Path) -> None:
    """Replace the running app with the downloaded payload and relaunch."""
    app_root = current_app_root()
    if app_root is None:
        raise UpdateError("Not running from an installed app; update manually.")
    if sys.platform == "darwin":
        _install_macos(payload, app_root)
    elif sys.platform == "win32":
        _install_windows(payload, app_root)
    else:
        raise UpdateError("Self-update is only supported on macOS and Windows.")


def _install_macos(dmg: Path, app_root: Path) -> None:
    mount = Path(tempfile.mkdtemp(prefix="editsync-dmg-"))
    try:
        subprocess.run(
            ["hdiutil", "attach", str(dmg), "-mountpoint", str(mount),
             "-nobrowse", "-quiet"],
            check=True,
        )
        source = mount / app_root.name
        if not source.is_dir():
            raise UpdateError(f"{app_root.name} not found in the update image.")
        # ditto preserves signatures/permissions; the running app keeps
        # executing from open file handles while we replace it
        subprocess.run(["rm", "-rf", str(app_root)], check=True)
        subprocess.run(["ditto", str(source), str(app_root)], check=True)
    except subprocess.CalledProcessError as exc:
        raise UpdateError(f"Install failed: {exc}") from exc
    finally:
        subprocess.run(
            ["hdiutil", "detach", str(mount), "-quiet"], check=False
        )
    subprocess.Popen(["open", "-n", str(app_root)])
    os._exit(0)


def _install_windows(zip_path: Path, install_dir: Path) -> None:
    import zipfile

    staging = Path(tempfile.mkdtemp(prefix="editsync-update-"))
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(staging)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdateError(f"Could not unpack the update: {exc}") from exc
    new_dir = staging / "EditSync"
    if not new_dir.is_dir():
        candidates = [d for d in staging.iterdir() if d.is_dir()]
        if len(candidates) != 1:
            raise UpdateError("Unexpected update layout.")
        new_dir = candidates[0]

    script = staging / "apply_update.bat"
    script.write_text(
        "@echo off\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        f'robocopy "{new_dir}" "{install_dir}" /MIR >nul\r\n'
        f'start "" "{install_dir}\\EditSync.exe"\r\n'
        f'rmdir /s /q "{staging}"\r\n'
    )
    subprocess.Popen(
        ["cmd", "/c", str(script)],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        close_fds=True,
    )
    os._exit(0)

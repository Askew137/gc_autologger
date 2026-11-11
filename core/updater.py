"""
Application auto-update module for GC Autologger.
Handles:
- Checking GitHub Releases API for new versions.
- Parsing and comparing semantic version strings.
- Downloading update packages with progress reporting.
- Installing updates:
  - For Windows standalone PyInstaller binary: downloads new .exe and coordinates atomic batch replacement.
  - For Git repository checkouts: executes fast git pull.
  - For source distributions: downloads and extracts release archive.
- Safe process restarting.
"""

import os
import sys
import re
import json
import shutil
import zipfile
import tempfile
import threading
import subprocess
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional, Tuple, Callable

from core.version import __version__ as APP_VERSION, GITHUB_REPO
from core.config import get_base_dir


@dataclass
class ReleaseInfo:
    tag_name: str
    version: str
    title: str
    body: str
    published_at: str
    html_url: str
    exe_asset_url: Optional[str] = None
    exe_asset_size: int = 0
    zip_asset_url: Optional[str] = None
    zip_asset_size: int = 0
    is_newer: bool = False


def parse_version(v: str) -> Tuple[int, ...]:
    """
    Parse a version string (e.g. 'v1.7.0', '1.8.2', 'v1.7') into a comparable tuple of integers.
    Examples:
        'v1.7.0' -> (1, 7, 0)
        '1.8.1' -> (1, 8, 1)
        '2.0'   -> (2, 0)
    """
    if not v:
        return (0,)
    cleaned = v.strip().lstrip("vV")
    tokens = re.split(r"[.\-_]", cleaned)
    parts = []
    for tok in tokens:
        m = re.match(r"^(\d+)", tok)
        if m:
            parts.append(int(m.group(1)))
        else:
            break
    return tuple(parts) if parts else (0,)


def is_newer_version(candidate_v: str, current_v: str = APP_VERSION) -> bool:
    """Check if candidate_v is strictly newer than current_v."""
    return parse_version(candidate_v) > parse_version(current_v)


def check_for_updates(current_version: str = APP_VERSION, timeout: int = 8) -> Tuple[Optional[ReleaseInfo], Optional[str]]:
    """
    Query GitHub API for the latest release.
    Returns (ReleaseInfo, None) on success, or (None, error_str) on failure.
    """
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"GC-Autologger-Updater/{current_version}",
            "Accept": "application/vnd.github.v3+json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                return None, f"GitHub API responded with status {response.status}"
            raw_data = response.read().decode("utf-8")
            data = json.loads(raw_data)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Fallback to releases list if 'latest' is not set
            return _check_from_releases_list(current_version, timeout)
        return None, f"GitHub HTTP error {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return None, f"Network error connecting to GitHub: {e.reason}"
    except Exception as e:
        return None, f"Failed to check for updates: {e}"

    return _build_release_info(data, current_version)


def _check_from_releases_list(current_version: str, timeout: int) -> Tuple[Optional[ReleaseInfo], Optional[str]]:
    """Fallback: query all releases and pick the first non-draft release."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"GC-Autologger-Updater/{current_version}",
            "Accept": "application/vnd.github.v3+json"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw_data = response.read().decode("utf-8")
            releases = json.loads(raw_data)
            if not isinstance(releases, list) or not releases:
                return None, "No releases found on GitHub repository"
            # Pick first valid release
            for rel in releases:
                if not rel.get("draft", False):
                    return _build_release_info(rel, current_version)
            return None, "No active releases found"
    except Exception as e:
        return None, f"Failed checking releases list: {e}"


def _build_release_info(data: dict, current_version: str) -> Tuple[Optional[ReleaseInfo], Optional[str]]:
    tag_name = data.get("tag_name", "").strip()
    if not tag_name:
        return None, "Release has no valid tag name"

    clean_version = tag_name.lstrip("vV")
    title = data.get("name") or tag_name
    body = data.get("body", "") or ""
    published_at = data.get("published_at", "")[:10]  # YYYY-MM-DD
    html_url = data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases/tag/{tag_name}")

    exe_url = None
    exe_size = 0
    zip_url = None
    zip_size = 0

    for asset in data.get("assets", []):
        asset_name = asset.get("name", "")
        download_url = asset.get("browser_download_url")
        size = int(asset.get("size", 0))

        if asset_name.lower().endswith(".exe"):
            exe_url = download_url
            exe_size = size
        elif asset_name.lower().endswith(".zip") and "windows" in asset_name.lower():
            zip_url = download_url
            zip_size = size
        elif asset_name.lower().endswith(".zip") and not zip_url:
            zip_url = download_url
            zip_size = size

    newer = is_newer_version(clean_version, current_version)

    info = ReleaseInfo(
        tag_name=tag_name,
        version=clean_version,
        title=title,
        body=body,
        published_at=published_at,
        html_url=html_url,
        exe_asset_url=exe_url,
        exe_asset_size=exe_size,
        zip_asset_url=zip_url,
        zip_asset_size=zip_size,
        is_newer=newer
    )
    return info, None


def download_file_with_progress(
    url: str,
    dest_path: str,
    progress_callback: Optional[Callable[[float, int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None
) -> Tuple[bool, str]:
    """
    Download a file from url to dest_path with percentage progress reporting.
    Downloads to a temporary .part file first to prevent corrupting existing files.
    """
    part_path = dest_path + ".part"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"GC-Autologger-Updater/{APP_VERSION}"}
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            total_size = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            block_size = 64 * 1024  # 64 KB

            os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
            with open(part_path, "wb") as f:
                while True:
                    if cancel_event and cancel_event.is_set():
                        f.close()
                        if os.path.exists(part_path):
                            os.remove(part_path)
                        return False, "Download cancelled"

                    chunk = response.read(block_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    if progress_callback:
                        pct = (downloaded / total_size * 100.0) if total_size > 0 else 0.0
                        progress_callback(pct, downloaded, total_size)

        # Move .part to final destination
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except Exception:
                pass
        shutil.move(part_path, dest_path)
        return True, "Download completed successfully"
    except Exception as e:
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except Exception:
                pass
        return False, f"Download failed: {e}"


def perform_update(
    release: ReleaseInfo,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> Tuple[bool, str]:
    """
    Execute update based on the current runtime environment:
    1. Standalone Windows Executable (sys.frozen) -> downloads new .exe & creates updater.bat
    2. Git repository checkout -> executes git pull
    3. Source package without Git -> downloads release zip and updates files
    """
    def _notify(pct: float, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    base_dir = get_base_dir()

    # --------------------------------------------------------------------------
    # Case 1: PyInstaller Frozen Executable (.exe)
    # --------------------------------------------------------------------------
    if getattr(sys, "frozen", False):
        _notify(5.0, "Preparing Windows executable update...")
        current_exe = os.path.abspath(sys.executable)
        exe_dir = os.path.dirname(current_exe)
        new_exe = os.path.join(exe_dir, "GC_Autologger.new.exe")

        download_url = release.exe_asset_url
        if not download_url and release.zip_asset_url:
            # Download zip and extract .exe
            _notify(10.0, "Downloading Windows release package (zip)...")
            temp_zip = os.path.join(exe_dir, "temp_update.zip")
            ok, err = download_file_with_progress(
                release.zip_asset_url,
                temp_zip,
                progress_callback=lambda p, d, t: _notify(10.0 + p * 0.7, f"Downloading: {d // 1024 // 1024} MB / {t // 1024 // 1024} MB ({int(p)}%)")
            )
            if not ok:
                return False, f"Failed downloading update package: {err}"

            _notify(85.0, "Extracting executable from zip...")
            try:
                with zipfile.ZipFile(temp_zip, "r") as zf:
                    exe_member = None
                    for name in zf.namelist():
                        if name.lower().endswith(".exe"):
                            exe_member = name
                            break
                    if not exe_member:
                        return False, "Could not find .exe inside release zip package"
                    with zf.open(exe_member) as src, open(new_exe, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            except Exception as e:
                return False, f"Failed extracting update package: {e}"
            finally:
                if os.path.exists(temp_zip):
                    try:
                        os.remove(temp_zip)
                    except Exception:
                        pass
        elif download_url:
            # Download direct .exe asset
            _notify(10.0, "Downloading updated GC_Autologger.exe...")
            ok, err = download_file_with_progress(
                download_url,
                new_exe,
                progress_callback=lambda p, d, t: _notify(10.0 + p * 0.75, f"Downloading: {d // 1024 // 1024} MB / {t // 1024 // 1024} MB ({int(p)}%)")
            )
            if not ok:
                return False, f"Failed downloading update: {err}"
        else:
            return False, f"No Windows executable asset found in release {release.tag_name}"

        _notify(90.0, "Creating update launcher script...")
        # Create helper batch script to replace running executable after process exits
        batch_path = os.path.join(exe_dir, "updater.bat")
        batch_content = (
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            ":: Wait 1 second for the running GC_Autologger.exe process to exit\r\n"
            "timeout /t 1 /nobreak >nul\r\n"
            ":retry\r\n"
            f'move /y "{new_exe}" "{current_exe}" >nul 2>&1\r\n'
            "if errorlevel 1 (\r\n"
            "    timeout /t 1 /nobreak >nul\r\n"
            "    goto retry\r\n"
            ")\r\n"
            f'start "" "{current_exe}"\r\n'
            'del "%~f0" >nul 2>&1\r\n'
        )
        try:
            with open(batch_path, "w", encoding="utf-8") as bf:
                bf.write(batch_content)
        except Exception as e:
            return False, f"Failed creating updater script: {e}"

        _notify(100.0, "Update downloaded successfully!")
        return True, "Aktualizace byla úspěšně stažena! Klikněte na 'Restartovat aplikaci' pro dokončení instalace."

    # --------------------------------------------------------------------------
    # Case 2: Git Repository checkout (.git directory exists)
    # --------------------------------------------------------------------------
    git_dir = os.path.join(base_dir, ".git")
    if os.path.isdir(git_dir) and shutil.which("git"):
        _notify(20.0, "Checking Git repository updates...")
        try:
            # 1. Fetch remote changes
            res_fetch = subprocess.run(
                ["git", "fetch", "--tags", "origin"],
                cwd=base_dir,
                capture_output=True,
                text=True,
                timeout=30
            )
            if res_fetch.returncode == 0:
                _notify(50.0, "Applying latest Git commits...")
                # Try pulling latest main branch
                res_pull = subprocess.run(
                    ["git", "pull", "--ff-only", "origin", "main"],
                    cwd=base_dir,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if res_pull.returncode == 0:
                    _notify(100.0, "Git update completed!")
                    return True, "Aplikace byla úspěšně aktualizována přes Git! Klikněte na 'Restartovat aplikaci' pro načtení změn."
        except Exception as e:
            print(f"Git update failed, falling back to source archive: {e}")

    # --------------------------------------------------------------------------
    # Case 3: Source package without Git (Download and extract release zip)
    # --------------------------------------------------------------------------
    _notify(10.0, "Downloading source release archive from GitHub...")
    archive_url = f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{release.tag_name}.zip"
    temp_dir = tempfile.mkdtemp(prefix="gc_autologger_update_")
    archive_path = os.path.join(temp_dir, "release.zip")

    ok, err = download_file_with_progress(
        archive_url,
        archive_path,
        progress_callback=lambda p, d, t: _notify(10.0 + p * 0.7, f"Downloading: {d // 1024} KB ({int(p)}%)")
    )
    if not ok:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return False, f"Failed downloading source archive: {err}"

    _notify(80.0, "Extracting and applying updated files...")
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(temp_dir)

        # Locate root directory inside zip (e.g. gc_autologger-1.7.0)
        extracted_root = None
        for item in os.listdir(temp_dir):
            full = os.path.join(temp_dir, item)
            if os.path.isdir(full) and item != "release.zip":
                extracted_root = full
                break

        if not extracted_root:
            return False, "Could not locate extracted source files"

        # Copy updated files over project directory, preserving user data
        EXCLUDE_ITEMS = {
            "config.json",
            "session_cookies.json",
            "runs",
            ".git",
            "venv",
            "__pycache__",
            ".DS_Store"
        }

        for root, dirs, files in os.walk(extracted_root):
            rel_path = os.path.relpath(root, extracted_root)
            if any(part in EXCLUDE_ITEMS for part in rel_path.split(os.sep)):
                continue

            target_dir = os.path.join(base_dir, rel_path) if rel_path != "." else base_dir
            os.makedirs(target_dir, exist_ok=True)

            for file_name in files:
                if file_name in EXCLUDE_ITEMS:
                    continue
                src_file = os.path.join(root, file_name)
                dst_file = os.path.join(target_dir, file_name)
                shutil.copy2(src_file, dst_file)

        _notify(100.0, "Files updated successfully!")
        return True, "Aplikace byla úspěšně aktualizována! Klikněte na 'Restartovat aplikaci' pro načtení změn."
    except Exception as e:
        return False, f"Failed applying source files: {e}"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def restart_application():
    """Restart the application process."""
    if getattr(sys, "frozen", False):
        exe_path = os.path.abspath(sys.executable)
        exe_dir = os.path.dirname(exe_path)
        batch_path = os.path.join(exe_dir, "updater.bat")
        new_exe = os.path.join(exe_dir, "GC_Autologger.new.exe")

        # If a pending updater batch exists, spawn it to swap executable and launch
        if os.path.exists(batch_path) and os.path.exists(new_exe):
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(["cmd.exe", "/c", batch_path], creationflags=creationflags)
            os._exit(0)
        else:
            subprocess.Popen([exe_path] + sys.argv[1:])
            os._exit(0)
    else:
        # Running as standard Python script
        args = [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:]
        subprocess.Popen(args)
        os._exit(0)


def cleanup_update_artifacts():
    """Clean up any temporary updater artifacts left from previous launches."""
    base_dir = get_base_dir()
    targets = (
        "GC_Autologger.new.exe",
        "GC_Autologger.old.exe",
        "GC_Autologger.exe.old",
        "updater.bat",
        "temp_update.zip"
    )
    for name in targets:
        p = os.path.join(base_dir, name)
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

"""Install generated maps directly into Beat Saber's CustomLevels folder."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any


def _steam_libraries() -> list[Path]:
    libs = [Path(r"C:\Program Files (x86)\Steam")]
    vdf = Path(r"C:\Program Files (x86)\Steam\config\libraryfolders.vdf")
    if vdf.is_file():
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                p = Path(m.group(1).replace("\\\\", "\\"))
                if p.is_dir() and p not in libs:
                    libs.append(p)
        except OSError:
            pass
    return libs


def find_custom_levels() -> Path | None:
    """Locate the Beat Saber CustomLevels folder (env var > Steam > Oculus)."""
    env = os.environ.get("BEATSABER_CUSTOM_LEVELS")
    if env and Path(env).is_dir():
        return Path(env)

    candidates: list[Path] = []
    for steam in _steam_libraries():
        candidates.append(
            steam / "steamapps" / "common" / "Beat Saber" / "Beat Saber_Data" / "CustomLevels"
        )
    candidates.append(
        Path(
            r"C:\Program Files\Oculus\Software\Software\hyperbolic-magnetism-beat-saber"
            r"\Beat Saber_Data\CustomLevels"
        )
    )
    candidates.append(
        Path(
            r"C:\Program Files\Oculus\Software\software\hyperbolic-magnetism-beat-saber"
            r"\Beat Saber_Data\CustomLevels"
        )
    )
    for c in candidates:
        if c.is_dir():
            return c
    return None


PROTECTED_CUSTOM_LEVEL_FOLDERS = frozenset({"Pacific_Coast_Highway", "Pacific_Coast_Highway_8"})


def _safe_folder_name(title: str) -> str:
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:60]
    return slug.strip("_") or "beatforge_map"


def install_generated_map_elevated(job_id: str, timeout: float = 120.0) -> Path:
    """Ask Windows for admin approval, then install one generated job."""

    if os.name != "nt":
        raise RuntimeError("Administrator-assisted install is only available on Windows.")
    if not re.fullmatch(r"[0-9a-f]{12}", job_id):
        raise ValueError("Invalid generated job id")

    root = Path(__file__).resolve().parents[2]
    job_dir = root / "data" / "jobs" / job_id
    result_path = job_dir / "install-result.json"
    if not (job_dir / "map.zip").is_file():
        raise FileNotFoundError("Generated map zip is missing")
    result_path.unlink(missing_ok=True)

    arguments = subprocess.list2cmdline(
        ["-m", "beatforge.install_elevated", "--job-id", job_id]
    )
    exit_code = _run_as_admin(sys.executable, arguments, root, timeout)
    if not result_path.is_file():
        if exit_code == 1223:
            raise RuntimeError("Windows administrator approval was cancelled.")
        raise RuntimeError(
            f"Administrator-assisted install failed with exit code {exit_code}."
        )

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Could not read the elevated install result.") from exc
    if not result.get("installed"):
        raise RuntimeError(str(result.get("error") or "Elevated install failed."))
    return Path(str(result["path"]))


def _run_as_admin(
    executable: str, arguments: str, cwd: Path, timeout: float
) -> int:
    """Run one process with Windows' runas prompt and return its exit code."""

    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", wintypes.LPVOID),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = arguments
    info.lpDirectory = str(cwd)
    info.nShow = 0
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == 1223:
            return error
        raise OSError(error, "Could not start the administrator-assisted install")

    try:
        wait_ms = max(1, min(int(timeout * 1000), 0xFFFFFFFE))
        wait_result = kernel32.WaitForSingleObject(info.hProcess, wait_ms)
        if wait_result == 0x00000102:
            raise TimeoutError(
                "Timed out waiting for Windows administrator approval and install."
            )
        if wait_result != 0:
            raise OSError(ctypes.get_last_error(), "Could not wait for elevated install")
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise OSError(ctypes.get_last_error(), "Could not read elevated exit code")
        return int(code.value)
    finally:
        if info.hProcess:
            kernel32.CloseHandle(info.hProcess)


def install_map(
    zip_path: str | Path,
    custom_levels: str | Path | None = None,
    *,
    title: str = "beatforge_map",
) -> Path:
    """Extract a map zip into CustomLevels/<unique folder>. Returns dest dir."""
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise FileNotFoundError(f"Map zip missing: {zip_path}")

    base: Path | None = Path(custom_levels) if custom_levels else find_custom_levels()
    if base is None or not base.is_dir():
        raise RuntimeError(
            "Beat Saber CustomLevels folder not found. Set BEATSABER_CUSTOM_LEVELS "
            "or install Beat Saber via Steam/Oculus."
        )

    stem = _safe_folder_name(title)
    dest = base / stem
    n = 1
    while n < 1000 and (
        dest.name in PROTECTED_CUSTOM_LEVEL_FOLDERS
        or (dest.exists() and any(dest.glob("*.dat")))
    ):
        dest = base / f"{stem}_{n}"
        n += 1
    dest.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            if member.startswith("/") or ".." in Path(member).parts:
                continue  # never escape the destination
            target = dest / member
            if member.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(member))
    return dest


def find_game_root() -> Path | None:
    env = os.environ.get("BEATSABER_GAME_ROOT")
    if env:
        root = Path(env)
        if (root / "Beat Saber.exe").is_file():
            return root
    levels = find_custom_levels()
    if levels is not None:
        root = levels.parent.parent
        if (root / "Beat Saber.exe").is_file():
            return root
    for candidate in (
        Path(r"C:\Program Files\Oculus\Software\Software\hyperbolic-magnetism-beat-saber"),
        Path(r"C:\Program Files\Oculus\Software\software\hyperbolic-magnetism-beat-saber"),
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\Beat Saber"),
        Path(r"C:\Program Files\Steam\steamapps\common\Beat Saber"),
    ):
        if (candidate / "Beat Saber.exe").is_file():
            return candidate
    return None


def launch_beat_saber() -> dict[str, Any]:
    """Start Beat Saber for a human headset session. Never records a clear."""

    root = find_game_root()
    if root is None:
        raise FileNotFoundError("Beat Saber.exe was not found. Set BEATSABER_GAME_ROOT.")
    executable = root / "Beat Saber.exe"
    subprocess.Popen([str(executable)], cwd=str(root), close_fds=True)
    return {
        "launched": True,
        "path": str(executable),
        "playedBySoftware": False,
        "message": "Beat Saber started for human headset testing. Record results yourself; software did not play or clear the map.",
    }

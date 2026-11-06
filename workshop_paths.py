"""Public-edition paths for user data and optional local tools."""
import os
from pathlib import Path
import sys
import tempfile

APP_ID = "AkasanWorkshopPublic"
CLI_NAME = "UberWolfCli.exe"


def program_directory():
    executable = sys.executable if getattr(sys, "frozen", False) else __file__
    return Path(executable).resolve().parent


def app_store():
    return Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / APP_ID


def archiver_path():
    override = os.environ.get("AKASAN_ARCHIVER")
    candidate = Path(override).expanduser() if override else program_directory() / "tools" / CLI_NAME
    if candidate.suffix.lower() != ".exe" or not candidate.is_file():
        raise FileNotFoundError("请将兼容的 UberWolfCli.exe 放到工坊旁的 tools 文件夹，或设置 AKASAN_ARCHIVER。")
    return candidate.resolve()


def scratch_root(section):
    override = os.environ.get("AKASAN_TEMP")
    base = Path(override).expanduser() if override else Path(tempfile.gettempdir())
    if not override and not str(base).isascii() and os.environ.get("PUBLIC"):
        base = Path(os.environ["PUBLIC"])
    folder = (base / APP_ID / section).resolve()
    if not str(folder).isascii():
        raise ValueError("打包工具需要英文临时路径，请将 AKASAN_TEMP 设为可写的英文路径。")
    folder.mkdir(parents=True, exist_ok=True)
    return folder

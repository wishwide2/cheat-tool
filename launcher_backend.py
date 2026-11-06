"""Transactional, number-preserving language switch for Akasan 9.51.

Nothing is changed on import. Only the fixed game targets, the modifier's
baseline, this launcher's own state and its dedicated backups can be written.
The database format is based on the supplied Akasan mod_core.py reader;
numeric values are patched in the selected language's plain byte stream.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import shutil
import struct
import subprocess
import uuid

import lz4.block
from workshop_paths import CLI_NAME, app_store, archiver_path, scratch_root


DAT_NAMES = ("DataBase.dat", "CommonEvent.dat", "Game.dat")
RESOURCE_NAMES = ("BasicData.wolf", "MapData.wolf") + tuple(
    "BasicData/" + name for name in DAT_NAMES
)
MAGIC = bytes.fromhex("00 57 00 00 4f 4c 55 46 4d 00")
GAME_ID = "akasan9.51"
SUPPORTED_LANGUAGES = frozenset(("zh", "ja", "en"))


class LauncherError(RuntimeError):
    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail


class AlreadyRunning(LauncherError):
    pass


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_record(path: Path) -> dict:
    return {"sha256": sha256(path), "size": path.stat().st_size}


def _same_path(first: Path | str, second: Path | str) -> bool:
    return os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second))


def _linked(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def safe_child(root: Path, relative: str) -> Path:
    """Return a strictly contained path; no absolute names, '..', links or junctions."""
    p = PurePosixPath(relative.replace("\\", "/"))
    if p.is_absolute() or not p.parts or any(x in ("..", ".") or ":" in x for x in p.parts):
        raise LauncherError("资源目录不完整，请重新放入完整的语言资源。", "Unsafe relative path: " + relative)
    root = root.resolve()
    target = root.joinpath(*p.parts)
    if not target.resolve().is_relative_to(root):
        raise LauncherError("目录位置异常，已停止切换。", str(target))
    cursor = target
    while cursor != root:
        if _linked(cursor):
            raise LauncherError("目录包含链接，无法安全切换。", str(cursor))
        cursor = cursor.parent
    return target


def _record_valid(record: dict) -> bool:
    if not isinstance(record, dict):
        return False
    digest, size = record.get("sha256"), record.get("size")
    return (isinstance(digest, str) and len(digest) == 64
            and all(c in "0123456789abcdef" for c in digest)
            and isinstance(size, int) and not isinstance(size, bool) and size >= 0)


def verify_file(path: Path, record: dict, user_message="语言资源缺失或损坏，请重新放入完整资源。"):
    if not _record_valid(record) or not path.is_file() or _linked(path):
        raise LauncherError(user_message, "Missing/invalid file: " + str(path))
    if path.stat().st_size != record["size"] or sha256(path) != record["sha256"]:
        raise LauncherError(user_message, "File identity mismatch: " + str(path))


def verify_wolf(path: Path):
    with path.open("rb") as stream:
        header = stream.read(0x40)
    # Original game uses DX archive v8 and cryptVersion 0; do not install v5 or v9.
    if (len(header) < 0x34 or header[:4] != b"DX\x08\x00"
            or struct.unpack_from("<H", header, 0x2E)[0] != 0):
        raise LauncherError("数据包版本不匹配，已停止切换。", "Unsupported archive header: " + str(path))


class Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def take(self, count: int) -> bytes:
        if count < 0 or self.pos + count > len(self.data):
            raise ValueError("Database read exceeds input")
        value = self.data[self.pos:self.pos + count]
        self.pos += count
        return value

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def count(self, maximum=100000) -> int:
        value = self.u32()
        if value > maximum:
            raise ValueError("Database count exceeds limit")
        return value

    def string(self) -> bytes:
        value = self.take(self.count(4 * 1024 * 1024))
        if value and value[-1] != 0:
            raise ValueError("Database string is not NUL terminated")
        return value


@dataclass
class Database:
    version: int
    plain: bytes
    schema: tuple
    numbers: tuple[int, ...]
    offsets: tuple[int, ...]
    strings: tuple[bytes, ...]


def read_database(raw: bytes) -> Database:
    if len(raw) < 15 or raw[:10] != MAGIC:
        raise ValueError("Not a WOLF DataBase.dat")
    version = raw[10]
    if version == 0xC4:
        if len(raw) < 19:
            raise ValueError("Truncated compressed database")
        plain_size, compressed_size = struct.unpack_from("<II", raw, 11)
        if plain_size > 128 * 1024 * 1024 or compressed_size != len(raw) - 19:
            raise ValueError("Invalid compressed database length")
        plain = lz4.block.decompress(raw[19:], uncompressed_size=plain_size)
        if len(plain) != plain_size:
            raise ValueError("Invalid decompressed database length")
    elif version == 0xC3:
        plain = raw[11:]
    else:
        raise ValueError("Unsupported database version")
    rd = Reader(plain)
    schema, numbers, offsets, strings = [], [], [], []
    for _ in range(rd.count(1000)):
        flag = rd.i32()
        spec = rd.i32() if flag <= -2 else None
        count = rd.count(1000) if flag <= -1 else flag
        if count < 0 or count > 1000:
            raise ValueError("Invalid database field count")
        config = tuple(rd.u32() for _ in range(count))
        if any(not 1000 <= value < 3000 for value in config):
            raise ValueError("Unsupported database field kind")
        numeric = tuple(i for i, value in enumerate(config) if value < 2000)
        text = tuple(i for i, value in enumerate(config) if value >= 2000)
        row_count = rd.count()
        schema.append((flag, spec, config, row_count))
        for _ in range(row_count):
            for _ in numeric:
                offsets.append(rd.pos)
                numbers.append(rd.i32())
            for _ in text:
                strings.append(rd.string())
    if rd.take(1) != bytes([version]) or rd.pos != len(plain):
        raise ValueError("Unexpected database trailer")
    return Database(version, plain, tuple(schema), tuple(numbers), tuple(offsets), tuple(strings))


def merge_database(current: Database, selected: Database) -> tuple[bytes | None, int]:
    if current.schema != selected.schema or len(current.numbers) != len(selected.numbers):
        raise LauncherError("当前游戏的数据结构与语言包不一致，已停止切换。", "UDB schema mismatch")
    changed = sum(a != b for a, b in zip(current.numbers, selected.numbers))
    if not changed:
        return None, 0
    merged = bytearray(selected.plain)
    for offset, number in zip(selected.offsets, current.numbers):
        struct.pack_into("<i", merged, offset, number)
    if selected.version == 0xC4:
        compressed = lz4.block.compress(bytes(merged), store_size=False)
        result = MAGIC + b"\xc4" + struct.pack("<II", len(merged), len(compressed)) + compressed
    else:
        result = MAGIC + b"\xc3" + bytes(merged)
    verified = read_database(result)
    if (verified.numbers != current.numbers or verified.strings != selected.strings
            or verified.schema != selected.schema):
        raise LauncherError("玩法数据保留检查未通过，已停止切换。", "Merged UDB read-back mismatch")
    return result, changed


def is_game_running(game_exe: Path) -> bool:
    """Read-only Win32 process metadata, restricted to the exact executable path."""
    if os.name != "nt":
        raise LauncherError("此启动器需要在 Windows 中运行。")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32FirstW.restype = kernel.Process32NextW.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise LauncherError("暂时无法检查游戏是否已退出，请稍后重试。", "Process snapshot failed")
    unknown = False
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel.Process32FirstW(handle, ctypes.byref(entry))
        if not more and ctypes.get_last_error() != 18:
            raise LauncherError("暂时无法检查游戏是否已退出，请稍后重试。", "Process enumeration failed")
        while more:
            if entry.szExeFile.casefold() == game_exe.name.casefold():
                process = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                if process:
                    try:
                        buffer = ctypes.create_unicode_buffer(32768)
                        length = wintypes.DWORD(len(buffer))
                        if kernel.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(length)):
                            if _same_path(buffer.value, game_exe):
                                return True
                        elif ctypes.get_last_error() not in (87, 6):
                            unknown = True
                    finally:
                        kernel.CloseHandle(process)
                elif ctypes.get_last_error() not in (87, 6):
                    unknown = True
            more = kernel.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    if unknown:
        raise LauncherError("暂时无法确认同名游戏是否已退出，请关闭游戏后重试。", "Cannot query a Game.exe image path")
    return False


def default_baseline() -> Path:
    return app_store() / "baseline"


@contextmanager
def switch_lock(path: Path):
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    locked = False
    try:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            locked = True
        except OSError as exc:
            raise LauncherError("另一个启动器正在切换语言，请稍后重试。", repr(exc)) from exc
        yield
    finally:
        if locked:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        stream.close()


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def tree_records(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob("*")):
        if _linked(path):
            raise LauncherError("数据目录包含链接，已停止切换。", str(path))
        if path.is_file():
            result[path.relative_to(root).as_posix()] = file_record(path)
    return result


@dataclass
class SwitchResult:
    language: str
    preserved_numeric_fields: int
    modified_numeric_fields: int
    repacked: bool
    backup_dir: Path | None
    replaced_files: int
    launched: bool = False
    unchanged: bool = False
    baseline_synced: bool = False


class LanguageSwitcher:
    def __init__(self, game_root: Path, *, baseline_root: Path | None = None,
                 cli_path: Path | None = None, temp_root: Path | None = None,
                 process_guard: Callable[[Path], bool] = is_game_running,
                 status: Callable[[str], None] = lambda _text: None,
                 before_replace: Callable[[int, Path], None] | None = None):
        self.root = Path(game_root).resolve()
        self.localization = safe_child(self.root, "Localization")
        candidate_baseline = Path(baseline_root) if baseline_root is not None else default_baseline()
        if _linked(candidate_baseline):
            raise LauncherError("修改器基线目录包含链接，无法安全切换。")
        self.baseline = candidate_baseline.resolve()
        if self.baseline == self.root or self.root.is_relative_to(self.baseline) or self.baseline.is_relative_to(self.localization):
            raise LauncherError("修改器基线目录位置异常，已停止切换。")
        self.sync_baseline = baseline_root is not None
        if baseline_root is None:
            # The modifier uses one shared store. A copied game must never change
            # the real game's baseline simply because it runs this launcher.
            try:
                state_path = self.baseline.parent / "state.json"
                if state_path.stat().st_size <= 2 * 1024 * 1024:
                    modifier_state = json.loads(state_path.read_text("utf-8"))
                    stored_game = modifier_state.get("game")
                    self.sync_baseline = isinstance(stored_game, str) and bool(stored_game) and _same_path(stored_game, self.root)
            except (OSError, ValueError, TypeError):
                pass
        self.cli = Path(cli_path) if cli_path else archiver_path()
        self.temp_root = Path(temp_root).resolve() if temp_root else None
        self.guard = process_guard
        self.status = status
        self.before_replace = before_replace  # Fault injection used only by the isolated checks.

    def _assert_stopped(self):
        if self.guard(safe_child(self.root, "Game.exe")):
            raise AlreadyRunning("游戏正在运行。请先退出游戏，再选择语言。")

    def _load_manifest(self) -> dict:
        manifest_path = safe_child(self.localization, "manifest.json")
        try:
            if manifest_path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Manifest exceeds limit")
            manifest = json.loads(manifest_path.read_text("utf-8"))
            if (manifest["schema_version"] != 1 or manifest["game_id"] != GAME_ID
                    or not {"zh", "ja"}.issubset(manifest["languages"])
                    or not set(manifest["languages"]).issubset(SUPPORTED_LANGUAGES)
                    or manifest["engine"]["file"] != "Game.exe"
                    or manifest["tool"]["file"] != CLI_NAME):
                raise ValueError("Unsupported manifest")
            shared = manifest["basic_shared_members"]
            if len(shared) != 501:
                raise ValueError("Expected 501 shared BasicData members")
            for name, record in shared.items():
                safe_child(self.localization, name)
                if name in DAT_NAMES or not _record_valid(record):
                    raise ValueError("Invalid shared member")
            for code, language in manifest["languages"].items():
                if set(language["files"]) != set(RESOURCE_NAMES):
                    raise ValueError("Language resource set differs")
                for name, record in language["files"].items():
                    verify_file(safe_child(self.localization, code + "/" + name), record)
                if set(language["basic_members"]) != set(DAT_NAMES):
                    raise ValueError("Language BasicData member set differs")
                for name, record in language["basic_members"].items():
                    if record != language["files"]["BasicData/" + name]:
                        raise ValueError("Loose DAT and member identity differ")
                if not isinstance(language["map_members"], dict) or len(language["map_members"]) != 212:
                    raise ValueError("MapData member count differs")
                for name, record in language["map_members"].items():
                    safe_child(self.localization, name)
                    if not _record_valid(record):
                        raise ValueError("Invalid MapData member")
                for archive in ("BasicData.wolf", "MapData.wolf"):
                    verify_wolf(safe_child(self.localization, code + "/" + archive))
            verify_file(safe_child(self.root, "Game.exe"), manifest["engine"], "游戏版本与语言包不一致，已停止切换。")
            verify_file(self.cli, manifest["tool"], "启动器的打包组件缺失或损坏，请重新放入完整启动器。")
            return manifest
        except LauncherError:
            raise
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise LauncherError("语言资源清单缺失或不完整，请重新放入完整资源。", repr(exc)) from exc

    def _new_work(self) -> Path:
        base = self.temp_root or scratch_root("languages")
        if not str(base).isascii() or _linked(base):
            raise LauncherError("无法建立临时工作目录，请将启动器放在可写的英文路径中重试。")
        base.mkdir(parents=True, exist_ok=True)
        work = safe_child(base, "switch_" + uuid.uuid4().hex)
        work.mkdir()
        (work / "launcher_owned.json").write_text(json.dumps({"owner": GAME_ID, "purpose": "language-switch"}), "utf-8")
        return work

    def _run_cli(self, work: Path, args: list[str], copied_cli: Path):
        if not str(work).isascii() or any(not arg.isascii() for arg in args):
            raise LauncherError("临时目录名称不受支持，已停止切换。")
        completed = subprocess.run([str(copied_cli), *args], cwd=work,
                                   capture_output=True, timeout=180,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with (work / "archive_tool.log").open("ab") as stream:
            stream.write(completed.stdout + completed.stderr)
        if completed.returncode != 0:
            raise LauncherError("准备语言数据时遇到问题，游戏文件尚未更改。", "Archive tool exit " + str(completed.returncode))

    def _unpack(self, source: Path, destination: Path, name: str, cli: Path) -> Path:
        destination.mkdir()
        archive = destination / (name + ".wolf")
        shutil.copy2(source, archive)
        if sha256(source) != sha256(archive):
            raise LauncherError("复制语言数据时检查未通过，已停止切换。")
        self._run_cli(destination, [str(archive)], cli)
        extracted = destination / name
        if not extracted.is_dir():
            raise LauncherError("语言数据无法解开，游戏文件尚未更改。", str(destination))
        return extracted

    def _verify_tree(self, root: Path, expected: dict):
        actual = tree_records(root)
        if actual != expected:
            mismatch = sorted(set(actual) ^ set(expected))[:10]
            mismatch += [name for name in actual.keys() & expected.keys() if actual[name] != expected[name]][:10]
            raise LauncherError("语言数据内容检查未通过，已停止切换。", "Member mismatch: " + repr(mismatch))

    def _read_db(self, path: Path) -> Database:
        try:
            return read_database(path.read_bytes())
        except (ValueError, OSError, struct.error, RuntimeError) as exc:
            raise LauncherError("游戏数据库无法安全读取，已停止切换。", repr(exc)) from exc

    def _prepare(self, code: str, manifest: dict, work: Path) -> tuple[list, int, int, bool]:
        self.status("正在检查语言资源…")
        cli = work / CLI_NAME
        shutil.copy2(self.cli, cli)
        verify_file(cli, manifest["tool"])
        selected = manifest["languages"][code]
        language_root = safe_child(self.localization, code)
        basic_expected = {**manifest["basic_shared_members"], **selected["basic_members"]}
        basic = self._unpack(language_root / "BasicData.wolf", work / "selected_basic", "BasicData", cli)
        self._verify_tree(basic, basic_expected)
        maps = self._unpack(language_root / "MapData.wolf", work / "selected_maps", "MapData", cli)
        self._verify_tree(maps, selected["map_members"])
        # Verify each loose DAT against the corresponding unpacked archive member too.
        for name in DAT_NAMES:
            verify_file(basic / name, selected["files"]["BasicData/" + name])
        selected_db = self._read_db(basic / "DataBase.dat")
        known_databases = {}
        for language_code, language in manifest["languages"].items():
            parsed = self._read_db(safe_child(self.localization, language_code + "/BasicData/DataBase.dat"))
            if parsed.schema != selected_db.schema or parsed.numbers != selected_db.numbers:
                raise LauncherError("各语言的原版玩法数据不一致，已停止切换。", "Resource UDB numeric baseline mismatch")
            known_databases[language_code] = parsed
        self.status("正在保留当前的玩法数值…")
        current_archive = safe_child(self.root, "Data/BasicData.wolf")
        current_map = safe_child(self.root, "Data/MapData.wolf")
        for archive in (current_archive, current_map):
            if not archive.is_file():
                raise LauncherError("游戏数据包缺失，请将启动器放在完整的游戏目录中。")
            verify_wolf(archive)
        if not any(file_record(current_map) == language["files"]["MapData.wolf"] for language in manifest["languages"].values()):
            raise LauncherError("地图包与语言资源不一致，已停止切换以保留现有内容。")
        original_archive_record = file_record(current_archive)
        original_map_record = file_record(current_map)
        current = self._unpack(current_archive, work / "current_basic", "BasicData", cli)
        current_records = tree_records(current)
        if set(current_records) != set(basic_expected):
            raise LauncherError("当前游戏的数据成员与语言包不一致，已停止切换。")
        for name, record in manifest["basic_shared_members"].items():
            if current_records[name] != record:
                raise LauncherError("当前游戏包含额外的数据改动，已停止切换以保留现有内容。", "Changed shared member: " + name)
        current_db = self._read_db(current / "DataBase.dat")
        known_language = any(
            current_db.schema == parsed.schema and current_db.strings == parsed.strings
            and all(current_records[name] == manifest["languages"][language_code]["basic_members"][name]
                    for name in ("CommonEvent.dat", "Game.dat"))
            for language_code, parsed in known_databases.items()
        )
        if not known_language:
            raise LauncherError("当前游戏含有未知的文字或事件改动，已停止切换以保留现有内容。")
        merged, changed = merge_database(current_db, selected_db)
        source_basic = work / "selected_basic" / "BasicData.wolf"
        source_map = work / "selected_maps" / "MapData.wolf"
        if merged is not None:
            self.status("正在生成所选语言的数据包…")
            layout = work / "repack_game"
            (layout / "Data").mkdir(parents=True)
            shutil.copytree(basic, layout / "Data" / "BasicData")
            (layout / "Data" / "BasicData" / "DataBase.dat").write_bytes(merged)
            shutil.copy2(safe_child(self.root, "Game.exe"), layout / "Game.exe")
            verify_file(layout / "Game.exe", manifest["engine"])
            # This executable is an archive layout marker passed to the CLI, never run.
            self._run_cli(layout, [str((layout / "Game.exe").resolve()), "-p", "3", "-o"], cli)
            source_basic = layout / "Data" / "BasicData.wolf"
            if not source_basic.is_file():
                raise LauncherError("玩法数值合并未能完成，游戏文件尚未更改。")
            verify_wolf(source_basic)
            verified = self._unpack(source_basic, work / "verify_merged", "BasicData", cli)
            expected_merged = dict(basic_expected)
            expected_merged["DataBase.dat"] = file_record(layout / "Data" / "BasicData" / "DataBase.dat")
            self._verify_tree(verified, expected_merged)
            merged_db = self._read_db(verified / "DataBase.dat")
            if (merged_db.numbers != current_db.numbers or merged_db.strings != selected_db.strings
                    or merged_db.schema != selected_db.schema):
                raise LauncherError("玩法数值复核未通过，游戏文件尚未更改。")
        verify_wolf(source_basic)
        targets = [(source_basic, safe_child(self.root, "Data/BasicData.wolf"), "game/Data/BasicData.wolf"),
                   (source_map, safe_child(self.root, "Data/MapData.wolf"), "game/Data/MapData.wolf")]
        baseline_members = safe_child(self.baseline, "BasicData")
        if self.sync_baseline and baseline_members.exists():
            actual = tree_records(baseline_members)
            if set(actual) != set(basic_expected):
                raise LauncherError("修改器基线不完整，已停止切换以保留现有设置。")
            for name, record in manifest["basic_shared_members"].items():
                if actual[name] != record:
                    raise LauncherError("修改器基线含有额外改动，已停止切换。", "Changed baseline shared member: " + name)
            member_names = DAT_NAMES
        elif self.sync_baseline:
            # A fresh modifier receives all 504 members, copied from the verified archive.
            member_names = sorted(basic_expected)
        else:
            member_names = ()
        if self.sync_baseline:
            targets.append((work / "selected_basic" / "BasicData.wolf", safe_child(self.baseline, "BasicData.wolf"),
                            "modifier_baseline/BasicData.wolf"))
            for name in member_names:
                targets.append((safe_child(basic, name), safe_child(baseline_members, name), "modifier_baseline/BasicData/" + name))
        # Snapshot the live inputs now; concurrent changes during preparation abort before install.
        self._input_records = [(current_archive, original_archive_record), (current_map, original_map_record),
                               (safe_child(self.root, "Game.exe"), manifest["engine"])]
        return targets, len(current_db.numbers), changed, merged is not None

    def _fast_result(self, code: str, manifest: dict) -> SwitchResult | None:
        """Launch an unchanged, previously checked mode without unpacking or backups."""
        language = manifest["languages"][code]
        current_basic = safe_child(self.root, "Data/BasicData.wolf")
        current_map = safe_child(self.root, "Data/MapData.wolf")
        if not current_basic.is_file() or not current_map.is_file():
            return None
        if file_record(current_map) != language["files"]["MapData.wolf"]:
            return None
        current_record = file_record(current_basic)
        manifest_digest = sha256(safe_child(self.localization, "manifest.json"))
        previous = {}
        try:
            state = safe_child(self.localization, "launcher_state.json")
            if state.stat().st_size <= 8192:
                previous = json.loads(state.read_text("utf-8"))
        except (OSError, ValueError, TypeError):
            pass
        original = current_record == language["files"]["BasicData.wolf"]
        verified_previous = (previous.get("last_language") == code
                             and previous.get("manifest_sha256") == manifest_digest
                             and previous.get("installed_basic") == current_record)
        if not original and not verified_previous:
            return None
        verify_wolf(current_basic)
        verify_wolf(current_map)
        selected_db = self._read_db(safe_child(self.localization, code + "/BasicData/DataBase.dat"))
        if self.sync_baseline:
            baseline_archive = safe_child(self.baseline, "BasicData.wolf")
            members = safe_child(self.baseline, "BasicData")
            if (not baseline_archive.is_file() or file_record(baseline_archive) != language["files"]["BasicData.wolf"]
                    or not members.is_dir() or tree_records(members) != {**manifest["basic_shared_members"], **language["basic_members"]}):
                return None
        self._assert_stopped()
        changed = 0 if original else previous.get("modified_numeric_fields", 0)
        if not isinstance(changed, int) or not 0 <= changed <= len(selected_db.numbers):
            return None
        new_state = {"schema_version": 1, "last_language": code,
                     "manifest_sha256": manifest_digest, "installed_basic": current_record,
                     "modified_numeric_fields": changed,
                     "updated_utc": datetime.now(timezone.utc).isoformat()}
        # Last choice belongs only to this launcher. No modifier state is written.
        if not verified_previous:
            _write_json(safe_child(self.localization, "launcher_state.json"), new_state)
        return SwitchResult(code, len(selected_db.numbers), changed, not original,
                            None, 0, unchanged=True, baseline_synced=self.sync_baseline)

    def _launch(self, result: SwitchResult, launch: bool) -> SwitchResult:
        if launch:
            self.status("语言已准备好，正在启动游戏…")
            self._assert_stopped()
            try:
                subprocess.Popen([str(safe_child(self.root, "Game.exe"))], cwd=self.root)
                result.launched = True
            except OSError as exc:
                raise LauncherError("语言已切换，但游戏未能启动。请在游戏目录中打开 Game.exe。", repr(exc)) from exc
        self.status("语言已切换，祝你游戏愉快。")
        return result

    def _transaction(self, targets: list, code: str, work: Path) -> tuple[Path, int]:
        self.status("正在备份并切换语言…")
        backup_root = safe_child(self.root, "LanguageBackups")
        backup_root.mkdir(exist_ok=True)
        backup = safe_child(backup_root, datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:12])
        backup.mkdir()
        prepared, journal = [], {"schema_version": 1, "game_root": str(self.root),
                                  "baseline_root": str(self.baseline), "language": code,
                                  "created_utc": datetime.now(timezone.utc).isoformat(),
                                  "status": "preparing", "files": []}
        written = []
        try:
            # All future bytes and all old bytes are copied and verified before any replacement.
            for source, target, relative in targets:
                record = file_record(source)
                old = file_record(target) if target.is_file() else None
                if old == record:
                    continue
                if target.exists() and not target.is_file():
                    raise LauncherError("目标位置不是普通文件，已停止切换。", str(target))
                saved = safe_child(backup, relative)
                if old is not None:
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, saved)
                    verify_file(saved, old)
                target.parent.mkdir(parents=True, exist_ok=True)
                stage = target.with_name(".akasan_language_" + uuid.uuid4().hex + ".tmp")
                with source.open("rb") as reader, stage.open("xb") as writer:
                    shutil.copyfileobj(reader, writer)
                    writer.flush()
                    os.fsync(writer.fileno())
                verify_file(stage, record)
                row = {"target": str(target), "backup_relative": relative,
                       "before": old, "after": record}
                journal["files"].append(row)
                prepared.append((stage, target, saved, row))
            _write_json(backup / "transaction.json", journal)
            self._assert_stopped()
            for path, record in self._input_records:
                verify_file(path, record, "游戏数据在准备期间发生了变化，请关闭修改器后重试。")
            for _, target, _, row in prepared:
                current = file_record(target) if target.is_file() else None
                if current != row["before"]:
                    raise LauncherError("游戏或修改器数据在准备期间发生了变化，请重试。", str(target))
            journal["status"] = "installing"
            _write_json(backup / "transaction.json", journal)
            for index, (stage, target, saved, row) in enumerate(prepared):
                if self.before_replace:
                    self.before_replace(index, target)
                os.replace(stage, target)
                written.append((target, saved, row))
                verify_file(target, row["after"])
            journal["status"] = "committed"
            _write_json(backup / "transaction.json", journal)
            return backup, len(written)
        except Exception as exc:
            errors = []
            for target, saved, row in reversed(written):
                try:
                    if row["before"] is None:
                        target.unlink()
                    else:
                        restore = target.with_name(".akasan_restore_" + uuid.uuid4().hex + ".tmp")
                        shutil.copy2(saved, restore)
                        verify_file(restore, row["before"])
                        os.replace(restore, target)
                        verify_file(target, row["before"])
                except Exception as rollback_exc:
                    errors.append({"target": str(target), "error": repr(rollback_exc)})
            journal["status"] = "rollback_failed" if errors else "rolled_back"
            journal["failure"] = repr(exc)
            journal["rollback_errors"] = errors
            try:
                _write_json(backup / "transaction.json", journal)
            except Exception:
                pass
            if errors:
                raise LauncherError("切换中断，部分文件未能自动恢复。请保留 LanguageBackups 文件夹，暂时不要启动游戏。", repr(errors)) from exc
            if isinstance(exc, LauncherError):
                raise
            raise LauncherError("切换未能完成，本次已替换的文件已恢复。请关闭游戏和修改器后重试。", repr(exc)) from exc
        finally:
            # Only individual, checked staging files created by this transaction are removed.
            for stage, _, _, _ in prepared:
                if stage.is_file():
                    stage.unlink(missing_ok=True)

    def switch(self, code: str, *, launch: bool = True) -> SwitchResult:
        if code not in SUPPORTED_LANGUAGES:
            raise LauncherError("请选择简体中文、日本語或 English。")
        self._assert_stopped()
        if not self.root.is_dir():
            raise LauncherError("找不到游戏目录，请将启动器放在 Game.exe 旁。")
        for loose in ("Data/BasicData", "Data/MapData"):
            if safe_child(self.root, loose).exists():
                raise LauncherError("游戏目录存在散装数据，请先恢复标准数据包布局再切换。", loose)
        # Resource validation precedes any creation of user-data directories.
        manifest = self._load_manifest()
        if code not in manifest["languages"]:
            raise LauncherError("当前游戏目录未安装所选语言资源，请更新完整语言包。")
        with switch_lock(safe_child(self.localization, ".launcher.lock")):
            self._assert_stopped()
            self.status("正在检查当前语言…")
            fast_result = self._fast_result(code, manifest)
            if fast_result:
                return self._launch(fast_result, launch)
            work = self._new_work()
            try:
                targets, count, changed, repacked = self._prepare(code, manifest, work)
                state = work / "launcher_state.json"
                state.write_text(json.dumps({"schema_version": 1, "last_language": code,
                                             "manifest_sha256": sha256(safe_child(self.localization, "manifest.json")),
                                             "installed_basic": file_record(targets[0][0]),
                                             "modified_numeric_fields": changed,
                                             "updated_utc": datetime.now(timezone.utc).isoformat()},
                                            ensure_ascii=False, indent=2), encoding="utf-8")
                targets.append((state, safe_child(self.localization, "launcher_state.json"), "launcher/launcher_state.json"))
                backup, written = self._transaction(targets, code, work)
                result = SwitchResult(code, count, changed, repacked, backup, written,
                                      baseline_synced=self.sync_baseline)
            except LauncherError:
                raise
            except Exception as exc:
                raise LauncherError("准备语言数据时遇到问题，游戏文件尚未更改。请确认资源完整且目录可以写入。", repr(exc)) from exc
            return self._launch(result, launch)

    def last_language(self) -> str | None:
        try:
            path = safe_child(self.localization, "launcher_state.json")
            if path.stat().st_size > 4096:
                return None
            value = json.loads(path.read_text("utf-8")).get("last_language")
            return value if value in SUPPORTED_LANGUAGES else None
        except (LauncherError, OSError, ValueError, TypeError):
            return None

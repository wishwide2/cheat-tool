"""Small file and preset helpers used by the desktop workshop."""
import datetime
import json
import os
from pathlib import Path
import re
import tempfile

SAVE_RE = re.compile(r'^SaveData(\d+)\.sav$', re.I)
BACKUP_RE = re.compile(r'^_备份_(\d{8}_\d{6})(?:_\d+)?_(SaveData\d+\.sav)$', re.I)
FONT_SCALES = {'标准': 1.0, '舒适': 1.15, '大字': 1.3}
CHARACTER_PRESETS = {
    '冒险起步': {4: 20, 7: 1000, 9: 200, 60: 200, 10: 100, 11: 100, 12: 100, 13: 100, 14: 80, 15: 100},
    '轻松探索': {4: 50, 7: 6000, 9: 999, 60: 200, 10: 500, 11: 500, 12: 500, 13: 500, 14: 150, 15: 100},
    '强力通关': {4: 99, 7: 20000, 9: 9999, 60: 200, 10: 2500, 11: 1500, 12: 2500, 13: 1500, 14: 300, 15: 100},
}

def load_preferences(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def save_preferences(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='workshop_', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def list_save_files(folder, backups=False):
    records = []
    if not folder:
        return records
    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return records
    pattern = BACKUP_RE if backups else SAVE_RE
    for path in entries:
        match = pattern.fullmatch(path.name)
        if not match or not path.is_file():
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        original = match[2] if backups else path.name
        slot_match = SAVE_RE.fullmatch(original)
        records.append({'path': str(path.resolve()), 'name': path.name, 'slot': int(slot_match[1]),
                        'mtime': info.st_mtime, 'size': info.st_size,
                        'updated': datetime.datetime.fromtimestamp(info.st_mtime).strftime('%Y-%m-%d %H:%M:%S')})
    return sorted(records, key=lambda row: (-row['mtime'], row['slot'], row['name']))

def free_save_path(folder):
    folder = Path(folder)
    used = {int(m[1]) for path in folder.iterdir() if (m := SAVE_RE.fullmatch(path.name))}
    for slot in range(1, 501):
        if slot not in used:
            return folder / f'SaveData{slot:02d}.sav'
    raise ValueError('1～500 号槽位都已有文件，请选择其他 Save 文件夹。')

def restore_backup_copy(source, folder, validate):
    """Restore into a free slot using exclusive creation; never replace progress."""
    source, folder = Path(source), Path(folder)
    if not BACKUP_RE.fullmatch(source.name):
        raise ValueError('请选择自动备份列表中的存档文件。')
    if not folder.is_dir():
        raise ValueError('目标 Save 文件夹不存在。')
    raw = source.read_bytes()
    validate(raw)
    for _ in range(500):
        destination = free_save_path(folder)
        try:
            with destination.open('xb') as stream:
                stream.write(raw)
        except FileExistsError:
            continue
        if destination.read_bytes() != raw:
            raise ValueError('恢复文件的读回校验失败，请保留备份并重试。')
        return str(destination)
    raise ValueError('找不到可用的新槽位。')

"""Find 9.51 chapter saves and publish new slots for the native loader."""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
from struct import unpack_from
from tempfile import TemporaryDirectory

from lz4.block import LZ4BlockError

from akasan_editor import decode_save, encode
from decode_ff import decode
from launcher_backend import safe_child, sha256 as file_sha256, verify_wolf
from mod_core import run_cli
from parse_variable_db import parse_db
from story_progress import carry_progress
from workshop_paths import scratch_root
from workshop_tools import SAVE_RE, free_save_path


# Keep the numbering and entry maps aligned with the 9.51 in-game chapter menu.
CHAPTERS = {
    1: ('洞窟初段（虫群区域）', (8, 9)),
    2: ('史莱姆区域', (15,)),
    3: ('妖精区域', (26,)),
    4: ('重返洞窟', (46,)),
    5: ('装甲虫区域', (61, 66)),
    6: ('土蜘蛛区域', (73,)),
    7: ('水母区域', (93,)),
    8: ('大鲸港', (103,)),
    9: ('遗迹岛', (116,)),
    10: ('丰收庭园', (128,)),
    11: ('黑之领域', (138,)),
    12: ('黑之领域深处', (140, 162)),
    13: ('主线终章', (161, 164, 165)),
    14: ('缇萝尔的家', (43,)),
    15: ('狐之里', (75,)),
}
CHAPTER_BY_MAP = {map_id: number for number, (_, maps) in CHAPTERS.items() for map_id in maps}
CHECKPOINT_RE = re.compile(r'AkasanStoryV1-(0[1-9]|1[0-5])\.sav', re.I)
SAVE_TITLE = '赤ずきんと不思議な洞窟'.encode('utf-8') + b'\0'
ENGINE_SHA = 'd02d8fb0f891cef9810e00bae0e67ed0c1fd79a61e22cc4e178a2aaaac12f106'
MAX_SAVE_BYTES = 16 * 1024 * 1024
STORY_STARTS = 'Localization/StoryStarts'

# Stock and title-repaired zh/ja/en resources. The older localized Game.dat
# titles are deliberately excluded: those titles can hang the Continue menu.
BASIC_ARCHIVES = frozenset((
    '20a2802940b71715458691e7dcc639a94efef39882da398508b0bcefb0ad7036',
    '331786ffa12b8eacae420bc734c2e92010f36b9425a8893313953fad8cd8900c',
    'be8b0cb1c4ce0ca5627f013d19d5186d7d3cd9af65eab7475a6b90a0ab871acf',
    'fb05badb373bead69938b5ee53c15960dbab842d379b646dbe7f48ca1f82321d',
    'cce5b1a5723342cb6a7ac948761d9741846fa0444f058541536315644b523758',
    '501c2baa9f39f493b50c788ec50ad10728da55e53af45d806b422485582629cc',
    '386356fa99d8ab04305172cdd77509bad655d5d685d583aa0b3548604cb93e63',
    '69936244427e5dad11e353e7b03692008c96f5c848e913b79d912a5fa4af73b6',
    '2271331e2fa933a95d760f06f3b40c87e8105714ddd11769824ee759c12d3bad',
    '142ce6fccaa85dffe1a9fdbdd8f43fd0feafb5fb8202fb7bce1c47d20b7911c7',
    '7ae558cc394cb05f0d062a6f1ab8c0808490cbb4711b46ebe6c66b50dc31c8a5',
))
GAME_CONFIGS = frozenset((
    '6c18d40f73d95befdf3c2af5a18137069aa4b3e93a53b9172e379095f7102550',
    'f541cd66ee19c7aaef0b33e75889e034a615d6fa5c58f1272ea2eca2d602dff9',
    'a3b8c5d08c4ec13e7fb6b8c13d47907006a9333b389eb045c1792ea6d3760bde',
))
STORY_MAP_ARCHIVES = frozenset((
    '4fd40a9f76a4c5d1ec6d5006a1a43cd523cd6f20e71e72017049315c94ca6b9d',
    'f834beb5e3081984d7d2685e98cf70386f2d46a7123a9051da65df608d241e0b',
    'b4b08f43b53aa9d7fdd1520919f429b269582e05a9bd60ef7eeb15343fd8ca55',
    '40f2ac602d25c27f4118fac6bf55df7e0b28ae17679ee9067690c9037a291239',
    'd53705b8a18a723c5c41e2f44293ed45e9ce445129603305ec8459313d9184ac',
    '660eb8088c8fbbda05e127b455e6a2d738af9d31e82254dbf378a7222d9a07e1',
    'de3faa91aafb30e3d58dbac0f1bc19b8d9fb51627a8680860ec6c508d64a8dfe',
    '55dd22a908010d14ea6789d4d832b73cff2521503fa5d4db87f7e73840ce74e3',
    '6de37bbab6b8b3edc9a26cf7a16d28302993b5283639c4d6d43cef12740193fd',
    'e0336c2a40c9da424d65860899ff6841ce19968eb9c14da5307d93eb937d05f8',
))


@dataclass(frozen=True)
class StorySave:
    path: Path
    chapter: int
    slot: int | None
    modified: float
    digest: str


@dataclass(frozen=True)
class StoryStart:
    chapter: int
    map_id: int
    level: int
    digest: str
    size: int

    @property
    def filename(self):
        return f'AkasanStartV1-{self.chapter:02d}.sav'


@dataclass
class StoryIndex:
    saves: dict[int, list[StorySave]]
    occupied_checkpoints: set[int]
    unreadable: list[str]
    has_title_menu: bool
    starts: dict[int, StoryStart]


def _checkpoint_name(chapter):
    return f'AkasanStoryV1-{chapter:02d}.sav'


def _require_engine(game):
    engine = safe_child(game, 'Game.exe')
    if not engine.is_file() or file_sha256(engine) != ENGINE_SHA:
        raise ValueError('剧情导航需要已验证的阿卡桑 9.51 游戏程序。')


def _has_title_menu(game):
    archive = safe_child(game, 'Data/MapData.wolf')
    return archive.is_file() and file_sha256(archive) in STORY_MAP_ARCHIVES


def _installed_starts(game):
    """Find chapter starts for the selected game's verified map pack."""
    try:
        manifest = safe_child(game, f'{STORY_STARTS}/manifest.json')
        if not manifest.is_file() or manifest.stat().st_size > 64 * 1024:
            return {}
        data = json.loads(manifest.read_text('utf-8'))
        if data['version'] != 1 or data['engine_sha256'] != ENGINE_SHA:
            return {}
        current = file_sha256(safe_child(game, 'Data/MapData.wolf'))
        if current not in STORY_MAP_ARCHIVES or current not in data['map_archives'].values():
            return {}
        result = {}
        for number in CHAPTERS:
            record = data['starts'].get(str(number))
            if not record:
                continue
            seed = StoryStart(number, record['map'], record['level'],
                              record['file']['sha256'], record['file']['size'])
            if (type(seed.map_id) is not int or seed.map_id not in CHAPTERS[number][1]
                    or type(seed.level) is not int or not 1 <= seed.level <= 99
                    or not isinstance(seed.digest, str) or not re.fullmatch(r'[0-9a-f]{64}', seed.digest)
                    or type(seed.size) is not int or not 0 < seed.size <= MAX_SAVE_BYTES):
                continue
            path = safe_child(game, f'{STORY_STARTS}/{seed.filename}')
            if path.is_file() and path.stat().st_size == seed.size:
                result[number] = seed
        return result
    except (OSError, ValueError, TypeError, AttributeError, KeyError, RuntimeError):
        return {}


def _require_save_identity(game):
    message = '无法确认当前游戏使用原版存档标识，已停止生成剧情存档。'
    loose = safe_child(game, 'Data/BasicData/Game.dat')
    if loose.exists() and (not loose.is_file() or file_sha256(loose) not in GAME_CONFIGS):
        raise ValueError(message)
    archive = safe_child(game, 'Data/BasicData.wolf')
    if not archive.is_file():
        if loose.is_file():
            return
        raise ValueError(message)
    if file_sha256(archive) in BASIC_ARCHIVES:
        return

    # Workshop gameplay rules can repack the archive without changing Game.dat.
    # Inspect a temporary copy with the same optional archiver as those rules.
    verify_wolf(archive)
    with TemporaryDirectory(prefix='identity_', dir=scratch_root('story')) as directory:
        work = Path(directory)
        shutil.copyfile(archive, work / archive.name)
        try:
            run_cli(work, ['.'])
        except FileNotFoundError as exc:
            raise ValueError(message + '如果应用过玩法规则，请配置兼容的 UberWolfCli。') from exc
        config = safe_child(work, 'BasicData/Game.dat')
        if not config.is_file() or file_sha256(config) not in GAME_CONFIGS:
            raise ValueError(message)


def _read_save_file(path):
    with path.open('rb') as stream:
        raw = stream.read(MAX_SAVE_BYTES + 1)
        modified = os.fstat(stream.fileno()).st_mtime
    if len(raw) > MAX_SAVE_BYTES:
        raise ValueError('存档超过可读取的大小。')
    return raw, modified


def _read_snapshot(path):
    raw, modified = _read_save_file(path)
    plain = decode(raw)
    title_size, = unpack_from('<H', plain, 1)
    title_end = 3 + title_size
    if plain[3:title_end] != SAVE_TITLE:
        raise ValueError('存档使用了不同的游戏标识。')
    # The UTF-8 FF payload puts the saved map ID after the title and
    # the ten-byte 9.51 map record prefix.
    if len(plain) < title_end + 14 or plain[title_end:title_end + 10] != b'\xa0' + bytes(9):
        raise ValueError('存档的地图记录格式无法识别。')
    map_id, = unpack_from('<i', plain, title_end + 10)
    return raw, plain, modified, map_id


def scan_story_saves(game):
    game = Path(game).resolve()
    _require_engine(game)
    folder = safe_child(game, 'Save')
    result = StoryIndex({number: [] for number in CHAPTERS}, set(), [], _has_title_menu(game),
                        _installed_starts(game))
    if not folder.exists():
        return result
    for path in sorted(folder.iterdir()):
        checkpoint = CHECKPOINT_RE.fullmatch(path.name)
        ordinary = SAVE_RE.fullmatch(path.name)
        if checkpoint:
            expected, slot = int(checkpoint[1]), None
            result.occupied_checkpoints.add(expected)
        elif ordinary and len(ordinary[1]) in (2, 3):
            expected, slot = None, int(ordinary[1])
            if not 1 <= slot <= 500 or path.name.casefold() != f'savedata{slot:02d}.sav':
                continue
        else:
            continue
        try:
            safe_child(folder, path.name)
            if not path.is_file():
                raise ValueError('不是普通存档文件。')
            raw, _, modified, map_id = _read_snapshot(path)
            chapter = CHAPTER_BY_MAP.get(map_id)
            if expected is not None and chapter != expected:
                raise ValueError('章节编号和地图不匹配。')
            if chapter is not None:
                result.saves[chapter].append(StorySave(path, chapter, slot, modified, sha256(raw).hexdigest()))
        except (OSError, RuntimeError, ValueError, LZ4BlockError):
            result.unreadable.append(path.name)
    for saves in result.saves.values():
        saves.sort(key=lambda save: (save.slot is not None, -save.modified, save.path.name))
    return result


def write_story_save(game, source, *, as_checkpoint=False):
    """Create a slot or a missing title checkpoint from a selected local save."""
    game = Path(game).resolve()
    _require_engine(game)
    folder = safe_child(game, 'Save')
    name = (_checkpoint_name(source.chapter) if source.slot is None
            else f'SaveData{source.slot:02d}.sav')
    path = safe_child(folder, source.path.name)
    if path != source.path or path.name.casefold() != name.casefold():
        raise ValueError('来源存档不属于当前游戏目录。')
    try:
        raw, plain, _, map_id = _read_snapshot(path)
    except (OSError, ValueError, LZ4BlockError) as exc:
        raise ValueError('来源存档无法读取，请刷新章节列表。') from exc
    chapter = CHAPTER_BY_MAP.get(map_id)
    if chapter != source.chapter or sha256(raw).hexdigest() != source.digest:
        raise ValueError('来源存档已有变动，请刷新章节列表后重试。')
    parse_db(plain)
    _require_save_identity(game)

    if as_checkpoint:
        if source.slot is None or not _has_title_menu(game):
            raise ValueError('请使用普通存档，并确认游戏已安装兼容的标题剧情导航。')
        target = folder / _checkpoint_name(chapter)
    else:
        target = None

    return _publish_story_copy(folder, raw, target)


def write_story_start(game, start, *, carry_from=None, source_digest=None):
    """Create a new slot at a verified start, optionally carrying player progress."""
    game = Path(game).resolve()
    _require_engine(game)
    if _installed_starts(game).get(start.chapter) != start:
        raise ValueError('这段剧情的起点资料已变动，请刷新列表。')
    path = safe_child(game, f'{STORY_STARTS}/{start.filename}')
    raw, plain, _, map_id = _read_snapshot(path)
    if map_id != start.map_id or len(raw) != start.size or sha256(raw).hexdigest() != start.digest:
        raise ValueError('这段剧情的起点文件校验失败，请重新安装本地剧情资源。')
    if carry_from is None:
        parse_db(plain)
    else:
        try:
            source_raw, _ = _read_save_file(Path(carry_from))
            source_plain = decode_save(source_raw)
        except (OSError, ValueError, LZ4BlockError) as exc:
            raise ValueError(f'无法读取携带存档 {Path(carry_from).name}。请选择阿卡桑 9.51 的存档。') from exc
        if source_digest is not None and sha256(source_raw).hexdigest() != source_digest:
            raise ValueError('打开的存档在磁盘上已有变动，请重新选择携带存档。')
        merged = carry_progress(source_plain, plain)
        raw = encode(raw, merged)
        if decode(raw) != merged:
            raise ValueError('跳转存档读回校验失败。')
    _require_save_identity(game)
    folder = safe_child(game, 'Save')
    folder.mkdir(exist_ok=True)
    return _publish_story_copy(folder, raw)


def _publish_story_copy(folder, raw, target=None):
    with TemporaryDirectory(prefix='.akasan-story-', dir=folder) as directory:
        staged = Path(directory) / 'copy.tmp'
        with staged.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if staged.read_bytes() != raw:
            raise ValueError('新存档读回校验失败，请重试。')

        for _ in range(500):
            destination = target or free_save_path(folder)
            try:
                # Same volume, atomic and without replacement. Windows rename
                # also works on exFAT, where hard links are unavailable.
                if os.name == 'nt':
                    staged.rename(destination)
                else:
                    os.link(staged, destination)
            except FileExistsError as exc:
                if target is not None:
                    raise ValueError('这段剧情已有章节档，请刷新列表。') from exc
                continue
            return destination
    raise ValueError('找不到可用的新槽位。')

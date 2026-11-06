"""阿卡桑数据包魔改核心：解包 / 改库 / 打回。

共用给「存档修改器」的魔改页和独立的「魔改开关」。
关键点：
  - 每次应用从所选安装的备份基线重新生成。
  - 打包固定用 UberWolfCli 的 -p 3（cryptVersion=0，与原包一致）；
  - 安装前校验包头的 cryptVersion，并自动备份旧包。
"""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

import lz4.block
from workshop_paths import CLI_NAME, app_store, archiver_path, scratch_root

STORE = str(app_store())
BASE_DIR = os.path.join(STORE, 'baseline')
BACKUP_DIR = os.path.join(STORE, 'backups')
STATE = os.path.join(STORE, 'state.json')

MAGIC = bytes.fromhex('00 57 00 00 4f 4c 55 46 4d 00')

# 敌人难度档位（属性倍率）
SCALES = [('原版（不改）', 1.0),
          ('×0.8 略弱', 0.8),
          ('×0.5 减半', 0.5),
          ('×0.25 很弱', 0.25),
          ('×1.5 更强', 1.5),
          ('×3 地狱', 3.0)]

# 我方增益档位（装备属性倍率）
EQUIP_SCALES = [('原版（不改）', 1.0),
                ('×1.5 小加强', 1.5),
                ('×2 明显变强', 2.0),
                ('×3 横扫', 3.0),
                ('×5 无敌', 5.0)]

MOD_LABELS = [
    ('price', '商店一律卖 1 元（道具 / 武器 / 防具）'),
    ('enemy', '敌人属性按下面档位缩放'),
    ('drop', '掉落率 100% + 经验金钱 ×3'),
    ('equip', '我方增益：装备属性按下面档位放大'),
    ('skill', '技能零消耗（SP/TP 改 0）'),
]


# ------------------------------------------------------------------ 数据库
class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n):
        part = self.data[self.pos:self.pos + n]
        if len(part) != n:
            raise ValueError('数据越界')
        self.pos += n
        return part

    def u32(self):
        return struct.unpack('<I', self.take(4))[0]

    def i32(self):
        return struct.unpack('<i', self.take(4))[0]

    def count(self, maximum=100000):
        v = self.u32()
        if v > maximum:
            raise ValueError('计数异常 %d' % v)
        return v

    def string(self):
        start = self.pos
        n = self.count(1000000)
        raw = self.take(n)
        if raw and raw[-1] != 0:
            raise ValueError('字符串未以 NUL 结尾')
        return {'offset': start, 'byte_length': n,
                'value': raw[:-1].decode('utf-8') if raw else ''}


def parse_database(raw):
    if len(raw) < 15 or raw[:10] != MAGIC:
        raise ValueError('不是解包后的 DataBase.dat')
    version = raw[10]
    if version == 0xC4:
        plain_size, comp_size = struct.unpack_from('<II', raw, 11)
        if comp_size != len(raw) - 19:
            raise ValueError('压缩长度不符')
        plain = lz4.block.decompress(raw[19:], uncompressed_size=plain_size)
    elif version == 0xC3:
        plain = raw[11:]
    else:
        raise ValueError('未知数据库版本 %#x' % version)
    rd = Reader(plain)
    types = []
    for _ in range(rd.count(1000)):
        flag = rd.i32()
        spec = rd.i32() if flag <= -2 else None
        field_count = rd.count(1000) if flag <= -1 else flag
        config = [rd.u32() for _ in range(field_count)]
        numeric = [i for i, v in enumerate(config) if 1000 <= v < 2000]
        strings = [i for i, v in enumerate(config) if 2000 <= v < 3000]
        rows = []
        for _ in range(rd.count()):
            fields = {}
            for fid in numeric:
                fields[fid] = {'kind': 'i32', 'offset': rd.pos, 'value': rd.i32()}
            for fid in strings:
                fields[fid] = {'kind': 'string', **rd.string()}
            rows.append(fields)
        types.append({'flag': flag, 'spec': spec, 'config': config, 'rows': rows})
    rd.take(1)
    return {'version': version, 'plain': plain, 'types': types}


def rebuild_plain(db):
    chunks = [struct.pack('<I', len(db['types']))]
    for t in db['types']:
        chunks.append(struct.pack('<i', t['flag']))
        if t['flag'] <= -2:
            chunks.append(struct.pack('<i', t['spec']))
        if t['flag'] <= -1:
            chunks.append(struct.pack('<I', len(t['config'])))
        chunks.extend(struct.pack('<I', v) for v in t['config'])
        chunks.append(struct.pack('<I', len(t['rows'])))
        for row in t['rows']:
            for fid, cfg in enumerate(t['config']):
                if cfg < 2000:
                    chunks.append(struct.pack('<i', row[fid]['value']))
            for fid, cfg in enumerate(t['config']):
                if cfg >= 2000:
                    f = row[fid]
                    raw = f['value'].encode('utf-8') + b'\0' if f['byte_length'] else b''
                    chunks.extend((struct.pack('<I', len(raw)), raw))
    chunks.append(bytes([db['version']]))
    return b''.join(chunks)


def pack_database(plain, version=0xC4):
    body = lz4.block.compress(plain, store_size=False)
    return MAGIC + bytes([version]) + struct.pack('<II', len(plain), len(body)) + body


# ------------------------------------------------------------------ 改动
def _name(t, r):
    f = t['rows'][r].get(0)
    return f['value'] if f and f['kind'] == 'string' else ''


def apply_mods(db, chosen, enemy_scale=0.5, equip_scale=1.5):
    log = []
    types = db['types']

    if 'price' in chosen:
        n = 0
        for tid, fid in ((2, 19), (3, 17), (4, 18)):
            t = types[tid]
            for r in range(len(t['rows'])):
                if not _name(t, r):
                    continue
                f = t['rows'][r][fid]
                if f['value'] > 1:
                    f['value'] = 1
                    n += 1
        log.append('商店价改 1 元：%d 项' % n)

    if 'enemy' in chosen and abs(enemy_scale - 1.0) > 1e-6:
        n = 0
        t = types[9]
        for r in range(len(t['rows'])):
            if not _name(t, r):
                continue
            for fid in (2, 4, 5, 6, 7, 8):
                f = t['rows'][r][fid]
                if f['value'] > 1:
                    new = int(round(f['value'] * enemy_scale))
                    f['value'] = max(1, new)
                    n += 1
        log.append('敌人属性 ×%g：%d 处' % (enemy_scale, n))

    if 'drop' in chosen:
        n = 0
        t = types[9]
        for r in range(len(t['rows'])):
            if not _name(t, r):
                continue
            row = t['rows'][r]
            for fid in (15, 17, 19):
                if 0 < row[fid]['value'] < 100:
                    row[fid]['value'] = 100
                    n += 1
            for fid in (12, 13):
                if row[fid]['value'] > 0:
                    row[fid]['value'] *= 3
                    n += 1
        log.append('掉落 100%% / 收益 x3：%d 处' % n)

    if 'equip' in chosen and abs(equip_scale - 1.0) > 1e-6:
        n = 0
        for tid, fids in ((3, (8, 9, 10, 11, 12)), (4, (5, 7, 8, 9, 10, 11))):
            t = types[tid]
            for r in range(len(t['rows'])):
                if not _name(t, r):
                    continue
                for fid in fids:
                    f = t['rows'][r][fid]
                    if f['value'] > 0:
                        f['value'] = int(round(f['value'] * equip_scale))
                        n += 1
        log.append('装备属性 ×%g：%d 处' % (equip_scale, n))

    if 'skill' in chosen:
        n = 0
        t = types[0]
        for r in range(len(t['rows'])):
            if not _name(t, r):
                continue
            f = t['rows'][r][5]
            if f['value'] != 0:
                f['value'] = 0
                n += 1
        log.append('技能零消耗：%d 个' % n)

    return log


# ------------------------------------------------------------------ 工具
def run_cli(workdir, args):
    tmp_cli = Path(workdir) / CLI_NAME
    shutil.copy2(archiver_path(), tmp_cli)
    p = subprocess.run([str(tmp_cli), *map(str, args)], cwd=workdir, capture_output=True, text=True,
                       encoding='utf-8', errors='replace',
                       stdin=subprocess.DEVNULL, timeout=120,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if p.returncode:
        raise RuntimeError(f'打包工具执行失败（退出码 {p.returncode}）')
    return p.stdout + p.stderr


def find_engine(game_dir):
    engine = Path(game_dir) / 'Game.exe'
    if not engine.is_file():
        raise RuntimeError('游戏目录里找不到 Game.exe')
    return engine


def wolf_of(game_dir):
    return os.path.join(game_dir, 'Data', 'BasicData.wolf')


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding='utf-8'))
        except Exception:
            pass
    return {'game': '', 'mods': [], 'scale': 0.5, 'equip_scale': 1.5, 'installed': False}


def save_state(st):
    os.makedirs(STORE, exist_ok=True)
    json.dump(st, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def check_baseline_owner(base, game_dir):
    owner = base / 'game_root.txt'
    if (not owner.is_file()
            or Path(owner.read_text(encoding='utf-8')) != Path(game_dir).resolve()):
        raise RuntimeError('现有玩法基线属于另一个游戏目录，请选择建立基线时的安装。')


def ensure_baseline(game_dir, say):
    find_engine(game_dir)
    base = Path(BASE_DIR)
    base_wolf = base / 'BasicData.wolf'
    base_dat = base / 'BasicData' / 'DataBase.dat'
    if base.exists():
        check_baseline_owner(base, game_dir)
        if not base_wolf.is_file() or not base_dat.is_file():
            raise RuntimeError('玩法基线不完整，请检查本地备份。')
        return base_wolf, base_dat

    src = Path(wolf_of(game_dir))
    if not src.is_file():
        raise RuntimeError('游戏目录里没有 Data/BasicData.wolf')
    base.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='unpack_', dir=scratch_root('mods')) as tmp, \
         tempfile.TemporaryDirectory(prefix='baseline_', dir=base.parent) as staging:
        work = Path(tmp)
        archive = work / 'BasicData.wolf'
        shutil.copy2(src, archive)
        run_cli(work, ['.'])
        extracted = work / 'BasicData'
        if not (extracted / 'DataBase.dat').is_file():
            raise RuntimeError('未能解包玩法数据库，请检查打包工具和游戏版本。')
        parse_database((extracted / 'DataBase.dat').read_bytes())
        prepared = Path(staging) / 'baseline'
        shutil.copytree(extracted, prepared / 'BasicData')
        shutil.copy2(archive, prepared / 'BasicData.wolf')
        (prepared / 'game_root.txt').write_text(str(Path(game_dir).resolve()), encoding='utf-8')
        os.rename(prepared, base)
    say('已为所选游戏建立玩法基线。')
    return base_wolf, base_dat


def replace_archive(source, destination):
    destination = Path(destination)
    if not destination.is_file():
        raise RuntimeError('游戏目录里没有可替换的 BasicData.wolf')
    backups = Path(BACKUP_DIR)
    backups.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='BasicData_', suffix='.wolf', dir=backups, delete=False) as backup:
        with destination.open('rb') as current:
            shutil.copyfileobj(current, backup)
    with tempfile.TemporaryDirectory(prefix='.akasan_archive_', dir=destination.parent) as staging:
        prepared = Path(staging) / 'BasicData.wolf'
        shutil.copy2(source, prepared)
        os.replace(prepared, destination)


def do_apply(game_dir, chosen, enemy_scale, equip_scale, say):
    _, base_dat = ensure_baseline(game_dir, say)
    db = parse_database(open(base_dat, 'rb').read())
    if rebuild_plain(db) != db['plain']:
        raise RuntimeError('基线自检失败')
    for m in apply_mods(db, chosen, enemy_scale, equip_scale):
        say('  · ' + m)
    new_dat = pack_database(rebuild_plain(db))

    dst = wolf_of(game_dir)
    with tempfile.TemporaryDirectory(prefix='pack_', dir=scratch_root('mods')) as tmp:
        g = Path(tmp)
        basic = g / 'Data' / 'BasicData'
        shutil.copytree(Path(BASE_DIR) / 'BasicData', basic)
        (basic / 'DataBase.dat').write_bytes(new_dat)
        engine = g / 'Game.exe'
        shutil.copy2(find_engine(game_dir), engine)
        run_cli(g, [engine, '-p', '3', '-o'])
        packed = g / 'Data' / 'BasicData.wolf'
        if not packed.is_file():
            raise RuntimeError('打包工具没有生成 BasicData.wolf')
        with packed.open('rb') as stream:
            head = stream.read(0x34)
        if len(head) < 0x34 or struct.unpack_from('<H', head, 0x2E)[0] != 0:
            raise RuntimeError('打包结果格式不匹配，已停止应用')
        check = g / 'verify'
        check.mkdir()
        shutil.copy2(packed, check)
        run_cli(check, ['.'])
        if (check / 'BasicData' / 'DataBase.dat').read_bytes() != new_dat:
            raise RuntimeError('重新解包后的数据库校验失败')
        replace_archive(packed, dst)
    say('已安装：%s（%d 字节）' % (dst, os.path.getsize(dst)))
    return dst


def do_restore(game_dir, say):
    base = Path(BASE_DIR)
    base_wolf = base / 'BasicData.wolf'
    if not base_wolf.is_file():
        raise RuntimeError('还没有基线，无法还原')
    check_baseline_owner(base, game_dir)
    find_engine(game_dir)
    dst = wolf_of(game_dir)
    replace_archive(base_wolf, dst)
    say('已还原原版数据包（%d 字节）' % os.path.getsize(dst))
    return dst


def status_lines(game_dir):
    lines = []
    lines.append('基线目录：%s %s' % (BASE_DIR, '（已建立）' if os.path.isdir(os.path.join(BASE_DIR, 'BasicData')) else '（未建立）'))
    if game_dir:
        cur = wolf_of(game_dir)
        if os.path.exists(cur):
            size = os.path.getsize(cur)
            base = os.path.join(BASE_DIR, 'BasicData.wolf')
            same = os.path.exists(base) and os.path.getsize(base) == size
            lines.append('当前数据包：%d 字节 %s' % (size, '＝原版' if same else '（已改版）'))
    return lines

def parse_scale(text, table):
    """把「×0.5 减半」「0.37」「50%」「2.8」都解析成倍率。"""
    raw = (text or '').strip()
    if not raw:
        raise ValueError('请填一个倍数，比如 0.37 或 2.8')
    cleaned = raw.replace('×', '').replace('*', '')
    if cleaned[:1] in ('x', 'X'):
        cleaned = cleaned[1:]
    cleaned = cleaned.strip()
    if cleaned.endswith('%'):
        try:
            return max(0.01, min(100.0, float(cleaned[:-1]) / 100.0))
        except ValueError:
            pass
    try:
        return max(0.01, min(100.0, float(cleaned)))
    except ValueError:
        pass
    for name, val in table:
        if name == raw:
            return val
    raise ValueError('看不懂这个倍数：%s（直接填数字，如 0.37、2.8、50%%）' % raw)
# ------------------------------------------------------------------ 自定义配置
def load_presets():
    return load_state().get('presets', {}) or {}


def save_preset(name, mods, scale, equip_scale):
    name = (name or '').strip()
    if not name:
        raise ValueError('配置名不能为空')
    st = load_state()
    presets = st.get('presets', {}) or {}
    presets[name] = {'mods': list(mods), 'scale': float(scale), 'equip_scale': float(equip_scale)}
    st['presets'] = presets
    save_state(st)
    return presets


def delete_preset(name):
    st = load_state()
    presets = st.get('presets', {}) or {}
    presets.pop(name, None)
    st['presets'] = presets
    save_state(st)
    return presets

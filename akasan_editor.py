#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阿卡桑 9.51 存档编辑与新槽位写入。
"""
import os
import re
import struct
import sys
import time
import traceback

try:
    import lz4.block  # noqa: F401
except Exception as exc:  # pragma: no cover
    print('缺少 lz4：pip install lz4==4.4.5', exc)
    raise

from decode_ff import decode
from parse_variable_db import parse_db
from parse_variable_region import parse_region
from chacha_pure_agent import crypt
try:
    import mod_core as MC
except Exception:
    MC = None

# ---- 内部坐标（不显示给用户） ----
T_MONEY = (6, 0, 0)
T_CHAR = 0
T_ITEM, T_WEAPON, T_ARMOR = 7, 8, 9
T_SKILL = 1
CHIP_F, ELEM_V = 0, 97
T_POLLUTION = ('pollution', 0, 20)
POLLUTION_MAX = 200
SAVE_TITLES = {
    name.encode('utf-8') + b'\0' for name in
    ('赤ずきんと不思議な洞窟', '小红帽与不可思议的洞窟', 'Akasan 9.51 - English')
}

CHAR_FIELDS = [
    ('level', 4, '等级'),
    ('hp_max', 7, '生命上限'),
    ('sp_max', 9, '魔力上限'),
    ('tp_max', 60, 'TP 上限'),
    ('atk', 10, '攻击力'),
    ('def', 11, '防御力'),
    ('int', 12, '智力'),
    ('mdef', 13, '魔法防御'),
    ('spd', 14, '速度'),
    ('hit', 15, '命中'),
]
EQUIP_SLOTS = [(28 + i, f'装备槽 {i + 1}') for i in range(8)] + [(68, '额外槽')]


# ---------------------------------------------------------------- 读写
def encode(header, plain):
    if len(plain) < 3 or plain[0] != 0x19 or plain[-1] != 0x19:
        raise ValueError('存档内容异常，已中止')
    comp = lz4.block.compress(plain, store_size=False)
    return crypt(header[:20] + struct.pack('<II', len(plain), len(comp)) + comp)


def decode_save(raw):
    plain = decode(raw)
    title_size, = struct.unpack_from('<H', plain, 1)
    if plain[3:3 + title_size] not in SAVE_TITLES:
        raise ValueError('此存档的游戏标识不匹配，请选择阿卡桑 9.51 的存档')
    return plain


def load_save(path):
    with open(path, 'rb') as stream:
        raw = stream.read()
    return raw, decode_save(raw)


def parse_all(plain):
    db = parse_db(plain)
    try:
        reg = parse_region(plain)
        bank0 = reg['numeric_banks'][0]
    except Exception:
        bank0 = None
    return db, bank0


def fld(db, t, r, f):
    f_ = db['types'][t]['rows'][r]['fields'].get(str(f))
    if f_ is None or f_['kind'] != 'i32':
        raise ValueError('目标字段不可用')
    return f_['offset'], f_['value']


def normalize_save_name(out_path):
    """把输出文件名规范成游戏能识别的 SaveDataNN.sav。

    游戏只认连续数字：1 号槽必须叫 SaveData01.sav，写成 SaveData1.sav 就读不到。
    返回 (最终路径, 是否改动过)
    """
    folder = os.path.dirname(os.path.abspath(out_path))
    base = os.path.basename(out_path)
    m = re.match(r'^SaveData0*([0-9]+)\.sav$', base, re.I)
    if m:
        n = int(m.group(1))
        fixed = f'SaveData{n:02d}.sav'
        return os.path.join(folder, fixed), fixed != base
    # 名字不合规范：找一个空槽位
    used = set()
    if os.path.isdir(folder):
        for f in os.listdir(folder):
            mm = re.match(r'^SaveData0*([0-9]+)\.sav$', f, re.I)
            if mm:
                used.add(int(mm.group(1)))
    for n in range(1, 501):
        if n not in used:
            return os.path.join(folder, f'SaveData{n:02d}.sav'), True
    return out_path, False


def apply_edits(src, out, edits):
    raw, plain = load_save(src)
    buf = bytearray(plain)
    for off, old, new in edits:
        cur = struct.unpack_from('<i', buf, off)[0]
        if cur != old:
            raise ValueError('存档内容与读取时不一致，已中止（未写入）')
        struct.pack_into('<i', buf, off, new)
    new_plain = bytes(buf)
    enc = encode(raw, new_plain)
    if decode(enc) != new_plain:
        raise ValueError('写回校验失败，已中止')
    if os.path.abspath(out) == os.path.abspath(src):
        raise ValueError('不能覆盖原存档，请换个文件名')
    if os.path.exists(out):
        raise ValueError('这个文件名已存在，请换一个')
    with open(out, 'xb') as f:
        f.write(enc)
    return len(enc)


def backup(path):
    stamp = time.strftime('%Y%m%d_%H%M%S')
    folder = os.path.dirname(os.path.abspath(path))
    with open(path, 'rb') as stream:
        data = stream.read()
    for sequence in range(100):
        suffix = f'_{sequence}' if sequence else ''
        dst = os.path.join(folder, f'_备份_{stamp}{suffix}_' + os.path.basename(path))
        try:
            with open(dst, 'xb') as stream:
                stream.write(data)
            return dst
        except FileExistsError:
            continue
    raise ValueError('自动备份名称冲突，请稍后重试')


# ---------------------------------------------------------------- 名称整理
def clean_name(s):
    parts = re.split(r'[\x00-\x1f]+', s)
    best = ''
    for p in parts:
        p = p.strip()
        p = re.sub(r'^[^0-9A-Za-z\u3040-\u30ff\u4e00-\u9fff]+', '', p)
        p = re.sub(r'[\x00-\x1f]', '', p).strip()
        if len(p) > len(best):
            best = p
    return best


def build_names(db):
    """Use display names already present in the save opened by the player."""
    names = {}
    for t in (19, 16, 10, 11):
        if t >= len(db['types']):
            continue
        for row in db['types'][t]['rows']:
            f = row['fields']
            if '1' not in f or '0' not in f:
                continue
            if f['1']['kind'] != 'i32' or f['0']['kind'] != 'string':
                continue
            iid = f['1']['value']
            nm = clean_name(f['0']['value'])
            if 10000 <= iid < 40000 and nm:
                names.setdefault(iid, nm)
    return names


# ---------------------------------------------------------------- GUI
def run_gui():
    from ui_app import EditorApp
    EditorApp(sys.modules[__name__]).run()


if __name__ == '__main__':
    try:
        run_gui()
    except Exception:
        traceback.print_exc()
        try:
            import tkinter.messagebox as mb
            mb.showerror('出错了', traceback.format_exc())
        except Exception:
            pass

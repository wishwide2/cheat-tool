"""Copy player-owned fields into a verified Akasan 9.51 chapter start."""
from struct import pack_into

from akasan_editor import ELEM_V, T_ARMOR, T_CHAR, T_ITEM, T_MONEY, T_POLLUTION, T_SKILL, T_WEAPON, fld
from parse_variable_db import parse_db
from parse_variable_region import parse_region


# 9.51 CDB: levels, vitals, growth, equipment, experience, TP and trap level.
CHAR_PROGRESS_FIELDS = (*range(4, 36), *range(54, 58), 59, 60, 66, 68)
CHAR_STATUS_TABLE = 17
# The movement/status UI reads these calculated values before any recalculation.
CHAR_STATUS_FIELDS = (*range(10), 21, 22, 24, 25, 26, 27)

# UDB item rows for consumables, crafting ingredients, ores and skill cards.
# Keys, special event items and unclassified IDs keep the destination value.
SUPPLY_IDS = (0, 1, 3, *range(5, 54), 65, 66, 81, 82, 83,
              *range(139, 186), *range(187, 220), 300, 301, *range(305, 327))


def carry_progress(source, destination):
    """Patch only the allowed numeric fields. All chapter state comes from destination."""
    source_db, destination_db = parse_db(source), parse_db(destination)
    source_types, destination_types = source_db['types'], destination_db['types']
    for table in (T_CHAR, T_SKILL, T_MONEY[0], T_ITEM, T_WEAPON, T_ARMOR, CHAR_STATUS_TABLE):
        if (table >= len(source_types) or table >= len(destination_types)
                or any(source_types[table][key] != destination_types[table][key]
                       for key in ('config', 'data_id_spec', 'row_count'))):
            raise ValueError('携带存档的角色或库存数据结构与 9.51 剧情起点不匹配。')

    merged = bytearray(destination)

    def copy(table, row, field):
        _, value = fld(source_db, table, row, field)
        offset, _ = fld(destination_db, table, row, field)
        pack_into('<i', merged, offset, value)

    copy(*T_MONEY)
    party = {fld(source_db, T_MONEY[0], 0, field)[1] for field in range(1, 7)}
    for character in sorted(party | {0}):
        if not 0 <= character < source_types[T_CHAR]['row_count']:
            continue
        for field in CHAR_PROGRESS_FIELDS:
            copy(T_CHAR, character, field)
        if character < source_types[T_SKILL]['row_count']:
            for field in range(source_types[T_SKILL]['field_count']):
                copy(T_SKILL, character, field)
        if character < source_types[CHAR_STATUS_TABLE]['row_count']:
            for field in CHAR_STATUS_FIELDS:
                copy(CHAR_STATUS_TABLE, character, field)

    for item in SUPPLY_IDS:
        copy(T_ITEM, item, 0)
    for table in (T_WEAPON, T_ARMOR):
        for item in range(source_types[table]['row_count']):
            copy(table, item, 0)

    source_bank = parse_region(source)['numeric_banks'][0]
    destination_bank = parse_region(destination)['numeric_banks'][0]
    for variable in (T_POLLUTION[2], ELEM_V):
        original = source_bank['values'][variable]
        target = destination_bank['values'][variable]
        pack_into('<i', merged, target['offset'], original['value'])
    return bytes(merged)

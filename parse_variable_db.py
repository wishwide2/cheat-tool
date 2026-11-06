"""Read-only inner CDB parser for the Akasan FF fixtures, not a save writer.

Layout reference: Sinflower/WolfSave, VariableDatabase.h and MemData.h (MIT).
No claim of complete parsing of the event/global-variable sections before CDB.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path
from decode_ff import decode


class ParseError(ValueError):
    pass


class Reader:
    def __init__(self, data, pos):
        self.data = data
        self.pos = pos

    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise ParseError(f'Out of bounds at {self.pos:#x}, size {n}')
        result = self.data[self.pos:self.pos+n]
        self.pos += n
        return result

    def u8(self):
        return self.take(1)[0]

    def u32(self):
        return struct.unpack('<I', self.take(4))[0]

    def i32(self):
        return struct.unpack('<i', self.take(4))[0]


def parse_type(rd, index):
    start = rd.pos
    flag = rd.i32()
    field_count = flag
    dis = None
    if flag <= -1:
        if flag <= -2:
            dis = rd.i32()
        field_count = rd.u32()
    if not 0 <= field_count <= 1000:
        raise ParseError(f'Invalid field count {field_count} at {start:#x}')
    config = [rd.u32() for _ in range(field_count)]
    if any(not 1000 <= n < 3000 for n in config):
        raise ParseError(f'Invalid storage config at {start:#x}')
    num_ids = [i for i, n in enumerate(config) if n < 2000]
    str_ids = [i for i, n in enumerate(config) if n >= 2000]
    # All fixtures observed use exact sequential storage indices per type.
    if ([config[i] for i in num_ids] != list(range(1000, 1000+len(num_ids)))
        or [config[i] for i in str_ids] != list(range(2000, 2000+len(str_ids)))):
        raise ParseError(f'Non-sequential storage config at {start:#x}')
    count_off = rd.pos
    row_count = rd.u32()
    if row_count > 100_000:
        raise ParseError(f'Invalid row count {row_count} at {count_off:#x}')
    rows = []
    for row_index in range(row_count):
        row_start = rd.pos
        fields = {}
        for field_id in num_ids:
            offset = rd.pos
            fields[str(field_id)] = {'offset': offset, 'kind': 'i32', 'value': rd.i32()}
        for field_id in str_ids:
            offset = rd.pos
            n = rd.u32()
            if n > 1_000_000:
                raise ParseError(f'Invalid string length {n} at {offset:#x}')
            raw = rd.take(n)
            if raw and raw[-1] != 0:
                raise ParseError(f'Missing NUL at {offset:#x}')
            try:
                value = (raw[:-1] if raw else b'').decode('utf-8')
            except UnicodeDecodeError as exc:
                raise ParseError(f'Invalid UTF-8 at {offset:#x}') from exc
            fields[str(field_id)] = {'offset': offset, 'kind': 'string',
                                     'byte_length': n, 'value': value}
        rows.append({'row': row_index, 'start': row_start, 'end': rd.pos, 'fields': fields})
    return {'type': index, 'start': start, 'end': rd.pos, 'flag': flag,
            'data_id_spec': dis, 'config': config, 'field_count': field_count,
            'number_count': len(num_ids), 'string_count': len(str_ids),
            'row_count_offset': count_off, 'row_count': row_count, 'rows': rows}


def parse_db(data):
    # A discovery signature, NOT a universal WOLF magic. Full validation follows.
    signature = struct.pack('<iiiiiii', -2, 1, 69, 2000, 2001, 2002, 2003)
    hits = []
    start = data.find(signature)
    while start >= 0:
        if start >= 5:
            rd = Reader(data, start - 5)
            flag = rd.u8()
            type_count = rd.u32()
            if 1 <= type_count <= 1000:
                try:
                    types = [parse_type(rd, i) for i in range(type_count)]
                    tail_start = rd.pos
                    tail_flag = rd.u8()
                    tail_count = 0
                    if tail_flag == 1:
                        tail_count = rd.u32()
                        if tail_count > 1_000_000:
                            raise ParseError('Invalid trailing array count')
                        for _ in range(tail_count):
                            tag = rd.u8()
                            rd.take(1 if tag < 0xfa else 4)
                    if rd.pos != len(data)-1 or data[rd.pos] != 0x19:
                        raise ParseError(f'Trailing boundary mismatch at {rd.pos:#x}')
                    hits.append({'start': start-5, 'end': tail_start, 'flag': flag,
                                 'type_count': type_count, 'types': types,
                                 'tail_start': tail_start, 'tail_flag': tail_flag,
                                 'tail_count': tail_count, 'eof_marker': rd.pos})
                except (ParseError, struct.error):
                    pass
        start = data.find(signature, start+1)
    if len(hits) != 1:
        raise ParseError(f'Expected one fully validated CDB, found {len(hits)}')
    return hits[0]


def parse_save(path):
    raw = Path(path).read_bytes()
    data = decode(raw)
    result = parse_db(data)
    result.update(source=str(Path(path).resolve()), sha256=hashlib.sha256(raw).hexdigest(),
                  payload_bytes=len(data), payload_sha256=hashlib.sha256(data).hexdigest())
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('save', type=Path)
    ap.add_argument('--out', type=Path)
    args = ap.parse_args()
    result = parse_save(args.save)
    print(f"payload={result['payload_bytes']}, CDB={result['start']:#x}..{result['end']:#x}, types={result['type_count']}, EOF={result['eof_marker']:#x}")
    for t in result['types']:
        examples = [{k: f['value'][:100] for k, f in r['fields'].items()
                     if f['kind'] == 'string' and f['value']} for r in t['rows'][:3]]
        print(f"T{t['type']:02d} {t['start']:#x}..{t['end']:#x} rows={t['row_count']} nums={t['number_count']} strs={t['string_count']} dis={t['data_id_spec']} examples={json.dumps(examples, ensure_ascii=False)}")
    if args.out:
        if args.out.resolve() == args.save.resolve():
            ap.error('Output cannot replace input')
        with args.out.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()

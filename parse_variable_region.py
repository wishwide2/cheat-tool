"""Read-only exploration of SavePart3 numeric/string variable collections.

Uses the old WolfSave layout as a hypothesis; validates all collection bounds
and UTF-8 strings. Whole-prefix parsing is deliberately not claimed.
"""
import json
import struct
import argparse
from pathlib import Path
from parse_variable_db import Reader, ParseError, decode

OUT=Path(__file__).parent
BANK_SIZES_951 = (365, 128, 55, 50, 60, 240, 24, 20, 169, 180)


def string(rd):
    off=rd.pos
    size=rd.u32()
    if not 1<=size<=1_000_000:
        raise ParseError(f'bad string size {size} at {off:#x}')
    raw=rd.take(size)
    if raw[-1]!=0:
        raise ParseError(f'bad string terminator at {off:#x}')
    value=raw[:-1].decode('utf-8')
    return {'offset':off,'byte_length':size,'value':value}


def parse_after_numeric_banks(data,string_start):
    rd=Reader(data,string_start)
    string_count=rd.u32()
    if not 1<=string_count<=10000:raise ParseError('bad string count')
    strings=[string(rd) for _ in range(string_count)]
    commons_start=rd.pos
    commons_count=rd.u32()
    if not 1<=commons_count<=10000:raise ParseError(f'bad common count {commons_count}')
    commons=[]
    for idx in range(commons_count):
        size=rd.u8()
        if size>100:raise ParseError('too many common values')
        vals=[]
        for fid in range(size):
            off=rd.pos
            vals.append({'field':fid,'offset':off,'value':rd.i32()})
        commons.append(vals)
    common_strings_start=rd.pos
    common_strings_count=rd.u32()
    if not 1<=common_strings_count<=10000:raise ParseError(f'bad common string count {common_strings_count}')
    common_strings=[]
    for idx in range(common_strings_count):
        size=rd.u8()
        if size>100:raise ParseError('too many common strings')
        vals=[string(rd) for _ in range(size)]
        common_strings.append(vals)
    return {'global_strings_start':string_start,'global_string_count':string_count,
            'global_strings':strings,'commons_start':commons_start,
            'common_count':commons_count,'commons':commons,
            'common_strings_start':common_strings_start,
            'common_string_count':common_strings_count,'common_strings':common_strings,
            'end':rd.pos}


def find_banks(data,end):
    candidates=[]
    # Return all hypotheses, including degenerate tails; selection requires evidence.
    for start in range(max(0,end-200000),end-8):
        count=struct.unpack_from('<I',data,start)[0]
        if not 1<=count<=20:continue
        p=start+4
        banks=[]
        for bi in range(count):
            if p+4>end:break
            n=struct.unpack_from('<I',data,p)[0]
            p+=4
            if not 1<=n<=100000 or p+4*n>end:break
            banks.append({'bank':bi,'count':n,'start':p})
            p+=n*4
        else:
            if p==end:candidates.append({'start':start,'banks':banks})
    return candidates


def with_numeric_banks(data, result, start, banks):
    result['numeric_banks_start'] = start
    for bank in banks:
        bank['values'] = [
            {'field': index, 'offset': bank['start'] + 4 * index, 'value': value}
            for index, value in enumerate(struct.unpack_from(f"<{bank['count']}i", data, bank['start']))
        ]
    result['numeric_banks'] = banks
    return result


def find_951_regions(data):
    """Identify early 9.51 saves whose first global string is still empty."""
    signature = struct.pack('<II', len(BANK_SIZES_951), BANK_SIZES_951[0])
    start = data.find(signature)
    matches = []
    while start >= 0:
        pos = start + 4
        banks = []
        try:
            for index, expected in enumerate(BANK_SIZES_951):
                count, = struct.unpack_from('<I', data, pos)
                pos += 4
                if count != expected:
                    raise ParseError('Unexpected 9.51 bank size')
                banks.append({'bank': index, 'count': count, 'start': pos})
                pos += 4 * count
            result = parse_after_numeric_banks(data, pos)
            if result['common_count'] == result['common_string_count'] == 620:
                matches.append(with_numeric_banks(data, result, start, banks))
        except (ParseError, UnicodeDecodeError, struct.error):
            pass
        start = data.find(signature, start + 1)
    return matches


def parse_region(data):
    # Discovery uses this game's S0 label, not a claimed WOLF format magic.
    marker=struct.pack('<I',5)+b'Elem\0'
    p=data.find(marker)
    matches=[]
    while p>=0:
        if p>=4:
            try:
                result=parse_after_numeric_banks(data,p-4)
                hypotheses=find_banks(data,p-4)
                if len(hypotheses)!=1 or len(hypotheses[0]['banks'])!=10:
                    raise ParseError('Expected one ten-bank numeric region')
                if result['common_count']!=result['common_string_count']:
                    raise ParseError('Common numeric/string count mismatch')
                bank_region = hypotheses[0]
                matches.append(with_numeric_banks(data, result, bank_region['start'], bank_region['banks']))
            except (ParseError,UnicodeDecodeError,struct.error):
                pass
        p=data.find(marker,p+1)
    if not matches:
        matches = find_951_regions(data)
    if len(matches)!=1:raise ParseError(f'Expected one variable region, found {len(matches)}')
    return matches[0]


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('save',type=Path)
    ap.add_argument('--out',type=Path)
    args=ap.parse_args()
    data=decode(args.save.read_bytes())
    result=parse_region(data)
    print('strings',hex(result['global_strings_start']),result['global_string_count'],'commonnum',hex(result['commons_start']),result['common_count'],
          'commonstrings',hex(result['common_strings_start']),result['common_string_count'],'end',hex(result['end']))
    for bank in result['numeric_banks']:
        hits=[(f['field'],hex(f['offset'])) for f in bank['values'] if f['value']==16]
        print('BANK',bank['bank'],hex(bank['start']),'count',bank['count'],'value16',hits)
    if args.out:
        with args.out.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2)


if __name__=='__main__':main()

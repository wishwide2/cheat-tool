"""Read the UTF-8 FF save envelope and validate its LZ4 payload."""
import struct
from chacha_pure_agent import crypt
import lz4.block


def decode(raw):
    if len(raw)<28 or raw[6]!=0x55 or raw[8]!=0xff:
        raise ValueError('Only UTF-8 FF-header saves are supported')
    stage=crypt(raw)
    usize,csize=struct.unpack_from('<II',stage,20)
    if csize!=len(stage)-28 or not 0<usize<100_000_000:
        raise ValueError('Decryption validation failed: invalid compressed sizes')
    plain=lz4.block.decompress(stage[28:],uncompressed_size=usize)
    if len(plain)!=usize or len(plain)<3 or plain[0]!=0x19 or plain[-1]!=0x19:
        raise ValueError('Unexpected save payload markers')
    return plain

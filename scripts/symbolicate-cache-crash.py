#!/usr/bin/env python3
"""Symbolicate the addresses of `[xnu] crash:` lines against the original dyld shared cache.

  symbolicate-cache-crash.py [--log CONSOLE] [--cache DIR] [--crash TEXT ...]

boot_progress_exit_fault (BootFaultProbe.c) prints `p<pid> <name> sig<n> PC<x> LR<x> E<x> F<x> +<slide> bt <x> ...`:
PC, LR, ESR, FAR and the frame walk's return addresses are raw user addresses of that process, and `<slide>` is the
process's task_shared_region_slide. An address belongs to the cache at `address - slide`, which is compared with the
imagesText of every sub-cache (the cache is split: the main file holds the header and little else). The local symbol
is the nearest one at or below that address, so a frame between two symbols reads as the symbol it follows. An address
outside the cache (a bundle image or JIT code) is reported as such rather than guessed.
"""
import argparse
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT / '.cache/firmware/target-volumes/systemos-mount/System/Library/Caches/com.apple.dyld'
# Offsets into dyld_cache_header of the pinned Darwin 23 format (refer/dyld cache-builder/dyld_cache_format.h).
IMAGES_TEXT_OFFSET, IMAGES_TEXT_COUNT = 136, 144
LOCAL_SYMBOLS_OFFSET, SHARED_REGION_START = 72, 224
IMAGE_TEXT_SIZE = 32
N_SECT = 0xe
CRASH = re.compile(r'\[xnu\] crash: p(?P<pid>\d+) (?P<name>\S+) sig(?P<sig>\d+) PC(?P<pc>[0-9a-f]+) '
                   r'LR(?P<lr>[0-9a-f]+) E(?P<esr>[0-9a-f]+) F(?P<far>[0-9a-f]+) \+(?P<slide>[0-9a-f]+)'
                   r'(?: bt(?P<bt>[0-9a-f ]*))?')


def cache_images(cache):
    """(load address, __TEXT size, path) of every image in the cache and its sub-caches."""
    images = []
    for path in sorted(p for p in cache.glob('dyld_shared_cache_arm64*') if p.suffix != '.symbols'):
        with path.open('rb') as stream:
            header = stream.read(1024)
            if header[:16].rstrip(b'\0') != b'dyld_v1   arm64':
                raise SystemExit(f'{path.name}: unexpected cache architecture')
            text_offset, text_count = struct.unpack_from('<2Q', header, IMAGES_TEXT_OFFSET)
            stream.seek(text_offset)
            blob = stream.read(text_count * IMAGE_TEXT_SIZE)
            for index in range(text_count):
                _, load, size, name_offset = struct.unpack_from('<16sQ2I', blob, index * IMAGE_TEXT_SIZE)
                stream.seek(name_offset)
                raw = stream.read(1024)
                images.append((load, size, raw[:raw.find(b'\0')].decode('utf-8', 'replace')))
    if not images:
        raise SystemExit(f'no dyld shared cache under {cache}; mount the manifest-selected SystemOS volume')
    return images


def cache_symbols(cache):
    """{image __TEXT offset in the shared region: its definitions sorted by address} from the local symbol table."""
    path = cache / 'dyld_shared_cache_arm64.symbols'
    if not path.exists():
        return None
    with path.open('rb') as stream:
        offset = struct.unpack_from('<Q', stream.read(1024), LOCAL_SYMBOLS_OFFSET)[0]
        if not offset:
            return None
        stream.seek(offset)
        nlist_offset, nlist_count, strings_offset, strings_size, entries_offset, entries_count = \
            struct.unpack('<6I', stream.read(24))
        stream.seek(offset + strings_offset)
        strings = stream.read(strings_size)
        stream.seek(offset + nlist_offset)
        nlist = stream.read(nlist_count * 16)
        stream.seek(offset + entries_offset)
        entries = [struct.unpack('<Q2I', stream.read(16)) for _ in range(entries_count)]

    def symbol(index):
        strx, kind, section, _, value = struct.unpack_from('<IBBHQ', nlist, index * 16)
        # Only a defined section symbol: the table also holds undefined and debug entries.
        if kind & 0xe0 or (kind & 0xe) != N_SECT or not section:
            return None
        end = strings.find(b'\0', strx)
        return value, strings[strx:end].decode('utf-8', 'replace')

    table = {}
    for dylib_offset, first, count in entries:
        table[dylib_offset] = sorted(filter(None, (symbol(i) for i in range(first, first + count))))
    return table


def nearest(definitions, unslid):
    """The definition at or below `unslid` (an image's own, sorted by address)."""
    low, high = 0, len(definitions)
    while low < high:
        middle = (low + high) // 2
        if definitions[middle][0] <= unslid:
            low = middle + 1
        else:
            high = middle
    return definitions[low - 1] if low else None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--log', type=Path, help='a pstore console file (ramoops text, NUL padded)')
    parser.add_argument('--cache', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--crash', action='append', help='a crash line to read instead of --log')
    args = parser.parse_args()
    lines = list(args.crash or [])
    if args.log:
        text = args.log.read_bytes().replace(b'\0', b'').decode('utf-8', 'replace')
        lines += [line for line in text.splitlines() if '[xnu] crash:' in line]
    if not lines:
        raise SystemExit('Nothing to read: pass --log or --crash')
    images = cache_images(args.cache)
    with (args.cache / 'dyld_shared_cache_arm64').open('rb') as stream:
        region = struct.unpack_from('<Q', stream.read(1024), SHARED_REGION_START)[0]
    symbols = cache_symbols(args.cache)
    for line in lines:
        match = CRASH.search(line)
        if not match:
            raise SystemExit(f'Not a crash line: {line}')
        slide = int(match['slide'], 16)
        esr = int(match['esr'], 16)
        syndrome = esr >> 26
        print(f"p{match['pid']} {match['name']} sig{match['sig']} "
              f"PC={int(match['pc'], 16):#x} LR={int(match['lr'], 16):#x} ESR={esr:#x} "
              f"(EC={syndrome:#x}{' instruction abort' if syndrome == 0x20 else ''}, ISS={esr & 0x3f:#x}) "
              f"FAR={int(match['far'], 16):#x} slide={slide:#x}")
        frames = [('PC', match['pc']), ('LR', match['lr'])]
        frames += [(f'bt{index}', value) for index, value in enumerate((match['bt'] or '').split())]
        for kind, text in frames:
            address = int(text, 16)
            if not address:
                print(f'  {kind:<3} 0 (a call through a null function pointer)')
                continue
            unslid = address - slide
            image = next((im for im in images if im[0] <= unslid < im[0] + im[1]), None)
            if not image:
                print(f'  {kind:<3} {address:#x} -> {unslid:#x} outside the cache')
                continue
            load, _, path = image
            note = ''
            hit = nearest(symbols[load - region], unslid) if symbols and load - region in symbols else None
            if hit:
                value, name = hit
                note = f'  {name}+{unslid - value:#x}'
            print(f'  {kind:<3} {address:#x} -> {unslid:#x} {path} +{unslid - load:#x}{note}')


if __name__ == '__main__':
    main()

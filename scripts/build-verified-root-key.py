#!/usr/bin/env python3
"""Archive the owner root key and the XNU crypto adapter, without the policy envelopes.

The envelopes (one 32-byte block hash per 4 KiB) are read from the userdata policy directory at run
time, so the kernel carries the 395-byte key instead of the 70 MB of hashes that made a 119 MiB kernel.
The key is pinned by --pin, and the archive is an extra link input of the kernel.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--key', type=Path, default=Path('build/verified-root-os/trusted-key.bin'),
                    help='the owner public anchor (395 bytes)')
parser.add_argument('--pin', help='the SHA-256 the key file must have')
parser.add_argument('--output', type=Path, default=Path('build/verified-root-key'),
                    help='output directory, relative to the build tree')
args = parser.parse_args()
key_path = args.key if args.key.is_absolute() else ROOT / args.key
if not key_path.is_file():
    raise SystemExit(f'the owner key is missing: {key_path}; build the volume policies first')
key = key_path.read_bytes()
if (len(key) != 395 or key[:8] != b'VROOTKEY' or not key[8] & 0x80 or
        not key[391] & 1 or key[392:] != b'\x01\x00\x01'):
    raise SystemExit('not a 3072-bit RSA owner anchor')
digest = hashlib.sha256(key).hexdigest()
if args.pin and digest != args.pin.lower():
    raise SystemExit(f'the owner key is {digest}, not the pinned {args.pin}')
out = args.output if args.output.is_absolute() else ROOT / args.output
out.mkdir(parents=True, exist_ok=True)
assembly = out / 'key.S'
assembly.write_text('''// Generated from the pinned owner anchor; the envelopes live in userdata.
.section __DATA_CONST,__const
.balign 8
.globl _verified_root_key
_verified_root_key:
.incbin "''' + str(key_path) + '''"
.balign 8
.globl _verified_root_policies, _verified_root_policy_count
_verified_root_policies:
.quad 0, 0
_verified_root_policy_count:
.quad 0
// The verified NativeVirtioBlock transport references these even when no virtio device exists on
// this board. A zero-length envelope is refused by vr_open, so it can never authorize a volume.
.balign 8
.globl _verified_root_manifest, _verified_root_manifest_bytes
_verified_root_manifest:
_verified_root_manifest_bytes:
.quad 0
''')
stub = out / 'probe-stub.c'
stub.write_text('''/* The device authenticates through the block driver; the QEMU crypto probe is not linked. */
void native_verified_root_probe(void);
volatile unsigned int native_verified_root_status = 1;
void native_verified_root_probe(void) { native_verified_root_status = 1; }
''')
recipe = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/bsd_init.o.json'
record = json.loads(recipe.read_text().strip().rstrip(','))
objects = []
for source in (ROOT / 'kits/VerifiedRoot/src/verified_root.c', ROOT / 'kits/VerifiedRoot/adapters/xnu_crypto.c', stub):
    destination = out / (source.stem + '.o')
    compile_args, command, index = record['arguments'], [], 0
    while index < len(compile_args):
        value = compile_args[index]
        if value == record['file']:
            command.append(str(source))
        elif value == '-o':
            command += ['-o', str(destination)]
            index += 1
        elif value in ('-MF', '-MJ'):
            index += 1
        elif value not in ('-MD', '-MP'):
            command.append(value)
        index += 1
    command += ['-I' + str(ROOT / 'kits/VerifiedRoot/include'), '-Werror']
    result = subprocess.run(command, cwd=record['directory'], capture_output=True, text=True)
    (out / (source.stem + '.log')).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit(result.stderr[-3500:])
    objects.append(destination)
sdk = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-c',
                str(assembly), '-o', str(out / 'key.o')], check=True)
objects.append(out / 'key.o')
for obj in objects:
    data = obj.read_bytes()
    if struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 1):
        raise SystemExit('Every key object must remain ordinary ARM64')
    cursor, deployment = 32, None
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        command, length = struct.unpack_from('<II', data, cursor)
        if command == 0x32:
            deployment = struct.unpack_from('<II', data, cursor + 8)
        cursor += length
    if deployment != (2, 0x110000):
        raise SystemExit('Every key object must target iOS 17')
archive = out / 'libVerifiedRootKey.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, *objects], check=True)
(out / 'key.json').write_text(json.dumps(dict(
    trusted_key_sha256=digest, trusted_key_bytes=len(key), key_file=str(key_path),
    archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
    policy_source='userdata SHNGPOL1 directory at run time',
    static_policy_count=0), indent=2) + '\n')
print(f'VerifiedRoot key archive: {archive} (key sha256 {digest})')

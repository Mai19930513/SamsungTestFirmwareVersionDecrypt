"""Explicit download/decrypt commands; never run by scheduled monitoring.

FUS protocol research: Bifrost ed73a034 (MIT), see THIRD_PARTY_NOTICES.md.
"""
import argparse
import base64
import hashlib
import os
import re
import shutil
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree
from urllib.parse import unquote

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from dotenv import load_dotenv

from .config import Settings
from .files import atomic_json, load_json, run_lock
from .fus import FusClient, build_request, parse_binary
from .network import FOTA_ROOT, FetchError, parse_stable, parse_xml, valid_version, validate_device

CHUNK = 4 * 1024 * 1024


def full_version(version):
    if not valid_version(version):
        raise ValueError('Invalid firmware version')
    parts = version.split('/')
    if len(parts) == 3:
        parts.append(parts[0])
    if not parts[2]:
        parts[2] = parts[0]
    return '/'.join(parts)


def logic_check(value, nonce):
    if len(value) < 16 or not nonce:
        raise ValueError('Invalid FUS logic-check input')
    return ''.join(value[ord(character) & 15] for character in nonce)


@dataclass
class Binary:
    metadata: dict
    key: bytes = field(repr=False)
    crc32: int | None = None
    model_type: str = ''


def binary_details(content, version, model, region, protocol='smart'):
    metadata = parse_binary(content, version, protocol)
    root = parse_xml(content)

    def value(name, section='Put'):
        return root.findtext(f'./FUSBody/{section}/{name}/Data', '').strip()

    reported = value('LATEST_FW_VERSION', 'Results') or value('BINARY_SW_VERSION', 'Results')
    put_version = value('LATEST_FW_VERSION') or value('BINARY_SW_VERSION')
    actual = reported or put_version
    if not valid_version(actual):
        raise ValueError('FUS did not return a firmware version')
    if put_version and full_version(actual) != full_version(put_version):
        raise ValueError('FUS returned conflicting firmware versions')
    metadata['version'] = actual
    filename = metadata['filename']
    if filename.endswith('.enc4'):
        logic = value('LOGIC_VALUE_FACTORY') or value('LOGIC_VALUE_HOME')
        if not logic or len(logic) > 256:
            raise ValueError('FUS did not return valid enc4 decryption parameters')
        key = hashlib.md5(logic_check(actual, logic).encode('ascii'), usedforsecurity=False).digest()
    elif filename.endswith('.enc2'):
        key = hashlib.md5(f'{region}:{model}:{actual}'.encode('ascii'), usedforsecurity=False).digest()
    else:
        key = b''
    crc = value('BINARY_CRC')
    if crc and not re.fullmatch(r'-?\d{1,10}', crc):
        raise ValueError('Invalid firmware CRC32')
    crc_number = int(crc) if crc else None
    if crc_number is not None and not -(1 << 31) <= crc_number < 1 << 32:
        raise ValueError('Invalid firmware CRC32')
    if metadata['encrypted'] and metadata['size_bytes'] % 16:
        raise ValueError('Encrypted firmware size is not AES block aligned')
    return Binary(metadata, key, crc_number & 0xffffffff if crc_number is not None else None, value('DEVICE_MODEL_TYPE'))


def build_init(binary, region, nonce, protocol='smart'):
    name = binary.metadata['filename']
    special = name.split('.')[0][-16:] if protocol == 'legacy' else name[-25:-9]
    root = ElementTree.Element('FUSMsg')
    header = ElementTree.SubElement(root, 'FUSHdr')
    for name_, text in (('ProtoVer', '1.0' if protocol == 'legacy' else '1'), ('SessionID', '0'), ('MsgID', '1')):
        ElementTree.SubElement(header, name_).text = text
    put = ElementTree.SubElement(ElementTree.SubElement(root, 'FUSBody'), 'Put')
    fields = {'BINARY_FILE_NAME' if protocol == 'legacy' else 'BINARY_NAME': name,
              'LOGIC_CHECK': logic_check(special, nonce)}
    if protocol == 'smart':
        fields.update(BINARY_SW_VERSION=binary.metadata['version'], DEVICE_LOCAL_CODE=region)
        if binary.model_type:
            fields['DEVICE_MODEL_TYPE'] = binary.model_type
    for name_, text in fields.items():
        ElementTree.SubElement(ElementTree.SubElement(put, name_), 'Data').text = text
    return ElementTree.tostring(root, encoding='utf-8')


class DownloadClient(FusClient):
    def __init__(self, settings, transport='requests'):
        super().__init__(settings)
        if transport == 'curl':
            from .curl_transport import CurlSession
            self.session.close()
            self.session = CurlSession()

    def latest(self, model, region):
        with self.session.get(f'{FOTA_ROOT}/{region}/{model}/version.xml', headers={'User-Agent': ''},
                              timeout=self.settings.timeout, allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise FetchError(f'fota_http_{response.status_code}')
            payload = bytearray()
            for block in response.iter_content(65536):
                payload.extend(block)
                if len(payload) > 8 * 1024 * 1024:
                    raise FetchError('response_too_large')
        return parse_stable(bytes(payload))['version']

    def information(self, model, region, version):
        validate_device(model, region)
        prefix = 'NF_SmartDownload' if self.protocol == 'smart' else 'NF_Download'
        self.request(prefix + 'GenerateNonce.do')
        if not self.nonce:
            raise FetchError('missing_nonce')
        body = build_request(full_version(version), model, region, self.nonce, self.protocol)
        return binary_details(self.request(prefix + 'BinaryInform.do', body), version, model, region, self.protocol)

    def initialize(self, binary, region):
        endpoint = 'NF_SmartDownloadBinaryInitForMass.do' if self.protocol == 'smart' else 'NF_DownloadBinaryInitForMass.do'
        result = parse_xml(self.request(endpoint, build_init(binary, region, self.nonce, self.protocol)))
        status = result.findtext('./FUSBody/Results/Status', '')
        if status not in {'S00', '200'}:
            raise FetchError('fus_download_initialization_failed')

    def stream(self, binary, start, end):
        authorization = f'FUS nonce="{self.nonce}", signature="{self.signature}", nc="", type="", realm=""'
        return self.session.get(unquote(binary.metadata['url']), headers={'Authorization': authorization,
            'User-Agent': 'SMART 2.0' if self.protocol == 'smart' else 'Kiss2.0_FUS',
            'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity'},
            timeout=self.settings.timeout, allow_redirects=False, stream=True)


def check_response(response, start, end, total):
    if response.status_code == 206:
        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
        if not match or tuple(map(int, match.groups())) != (start, end, total):
            raise ValueError('Server returned an incorrect Content-Range; partial file preserved')
    elif response.status_code == 200 and start == 0 and end == total - 1:
        pass
    else:
        raise ValueError(f'Server did not honor download range (HTTP {response.status_code}); partial file preserved')
    length = response.headers.get('Content-Length')
    if length and (not length.isdecimal() or int(length) != end - start + 1):
        raise ValueError('Server returned an incorrect Content-Length')
    if response.headers.get('Content-Encoding', 'identity').lower() not in ('identity', ''):
        raise ValueError('Unexpected encoded firmware response')


def parse_md5(value):
    if not value:
        return None
    if re.fullmatch(r'[a-fA-F0-9]{32}', value):
        return value.lower()
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError:
        raise ValueError('Invalid server Content-MD5') from None
    if len(raw) != 16:
        raise ValueError('Invalid server Content-MD5')
    return raw.hex()


def verify_file(path, size, crc=None, md5=None):
    if path.stat().st_size != size:
        raise ValueError('Firmware size does not match server metadata')
    actual_crc = 0
    digest = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(CHUNK):
            actual_crc = zlib.crc32(block, actual_crc)
            digest.update(block)
            sha256.update(block)
    if crc is not None and actual_crc != crc:
        raise ValueError('Firmware CRC32 mismatch; file retained for inspection')
    if md5 is not None and digest.hexdigest() != md5:
        raise ValueError('Firmware MD5 mismatch; file retained for inspection')
    return {'size_bytes': size, 'crc32': actual_crc, 'md5': digest.hexdigest(), 'sha256': sha256.hexdigest()}


class Progress:
    def __init__(self, label):
        self.label = label
        self.last = self.started = time.monotonic()
        self.initial = None

    def __call__(self, current, total):
        if self.initial is None:
            self.initial = current
        now = time.monotonic()
        if now - self.last >= 5 or current == total:
            speed = (current - self.initial) / max(now - self.started, 0.001) / 1048576
            print(f'{self.label}: {current:,}/{total:,} bytes ({current / total:.1%}), {speed:.1f} MiB/s', flush=True)
            self.last = now


def download_file(client, binary, region, directory, retries=2, progress=None):
    metadata = binary.metadata
    destination = directory / metadata['filename']
    partial = directory / (metadata['filename'] + '.part')
    manifest = directory / (metadata['filename'] + '.download.json')
    identity = {name: metadata[name] for name in ('filename', 'version', 'size_bytes', 'url')}
    identity['crc32'] = binary.crc32
    for path in (destination, partial, manifest):
        if path.is_symlink():
            raise ValueError('Refusing to write through a symlink')
    saved = load_json(manifest)
    if (partial.exists() or destination.exists()) and saved.get('identity') != identity:
        raise ValueError('Existing file has no matching download manifest; choose another output directory')
    state = {'identity': identity, 'md5': saved.get('md5')}
    total = metadata['size_bytes']
    if destination.exists():
        verified = verify_file(destination, total, binary.crc32, state['md5'])
        if partial.exists():
            raise ValueError('Both completed and partial files exist; inspect them before continuing')
        return destination, verified
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > total:
        raise ValueError('Partial firmware is larger than the server file')
    if shutil.disk_usage(directory).free < total - offset + 64 * 1024 * 1024:
        raise ValueError('Not enough free space for encrypted firmware')
    atomic_json(manifest, state)
    for attempt in range(retries + 1):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == total:
            break
        try:
            client.initialize(binary, region)
            with client.stream(binary, offset, total - 1) as response:
                check_response(response, offset, total - 1, total)
                digest = parse_md5(response.headers.get('Content-MD5'))
                # Content-MD5 on a range may cover only that range, so only use
                # the full representation's digest as a full-file checksum.
                if offset == 0 and digest:
                    state['md5'] = digest
                    atomic_json(manifest, state)
                with partial.open('ab') as output:
                    for block in response.iter_content(CHUNK):
                        if offset + len(block) > total:
                            raise ValueError('Server sent more firmware bytes than expected')
                        output.write(block)
                        offset += len(block)
                        if progress:
                            progress(offset, total)
                    output.flush()
                    os.fsync(output.fileno())
            if offset != total:
                raise requests.ConnectionError('incomplete_firmware_transfer')
            break
        except requests.RequestException:
            if attempt == retries:
                raise RuntimeError('Download interrupted; run the same command to resume') from None
            print(f'Download interrupted; retry {attempt + 1}/{retries} from saved offset.', flush=True)
            # Reauthenticate without changing the chosen firmware or file.
            prefix = 'NF_SmartDownload' if client.protocol == 'smart' else 'NF_Download'
            client.request(prefix + 'GenerateNonce.do')
    print('Checking downloaded size and checksums...', flush=True)
    verified = verify_file(partial, total, binary.crc32, state['md5'])
    os.replace(partial, destination)
    return destination, verified


def decrypt_file(source, destination, key, progress=None):
    if len(key) != 16:
        raise ValueError('Firmware AES key must contain 16 bytes')
    if source.resolve() == destination.resolve():
        raise ValueError('Encrypted input and decrypted output must be different files')
    partial = destination.with_name(destination.name + '.part')
    if source.is_symlink() or destination.is_symlink() or partial.is_symlink():
        raise ValueError('Refusing to use a symlink for firmware decryption')
    if destination.exists() or partial.exists():
        raise ValueError('Decrypted output or its temporary file already exists; choose another output filename')
    size = source.stat().st_size
    if not size or size % 16:
        raise ValueError('Encrypted firmware is empty or not AES block aligned')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < size + 64 * 1024 * 1024:
        raise ValueError('Not enough free space for decrypted firmware')
    cipher = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    try:
        with source.open('rb') as encrypted, partial.open('wb') as output:
            count = 0
            while block := encrypted.read(CHUNK):
                decoded = cipher.update(block)
                if count == 0 and not decoded.startswith((b'PK\x03\x04', b'PK\x05\x06')):
                    raise ValueError('Decryption did not produce a ZIP header; wrong key or file')
                output.write(decoded)
                count += len(block)
                if progress:
                    progress(count, size)
            output.write(cipher.finalize())
            output.flush()
            os.fsync(output.fileno())
        # Samsung encrypts AES blocks without standard padding removal. ZIP
        # tolerates trailing block padding; validate every member before publish.
        print('Checking ZIP structure and every member CRC...', flush=True)
        with zipfile.ZipFile(partial) as archive:
            if not archive.infolist():
                raise ValueError('Decrypted firmware ZIP is empty')
            failed = archive.testzip()
            if failed:
                raise ValueError('Decrypted ZIP member checksum failed')
            entries = len(archive.infolist())
        os.replace(partial, destination)
        return {'zip_entries': entries, 'zip_crc_verified': True, 'size_bytes': size}
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def probe(client, binary, region):
    size = binary.metadata['size_bytes']
    count = min(size, 1024 * 1024)
    client.initialize(binary, region)
    with client.stream(binary, 0, count - 1) as response:
        check_response(response, 0, count - 1, size)
        sample = bytearray()
        for block in response.iter_content(65536):
            sample.extend(block)
            if len(sample) > count:
                raise ValueError('Probe response exceeded the requested byte range')
    if len(sample) != count:
        raise ValueError('Probe download was incomplete')
    # Check a non-zero range too, exercising the server side of resume.
    resume_start = 16
    resume_end = min(count, 65552) - 1
    with client.stream(binary, resume_start, resume_end) as response:
        check_response(response, resume_start, resume_end, size)
        resumed = bytearray()
        for block in response.iter_content(65536):
            resumed.extend(block)
            if len(resumed) > resume_end - resume_start + 1:
                raise ValueError('Resume probe exceeded the requested byte range')
    if resumed != sample[resume_start:resume_end + 1]:
        raise ValueError('Resumed range differs from the initial downloaded sample')
    if binary.key:
        cipher = Cipher(algorithms.AES(binary.key), modes.ECB()).decryptor()
        plaintext = cipher.update(bytes(sample)) + cipher.finalize()
    else:
        plaintext = sample
    if not plaintext.startswith(b'PK\x03\x04'):
        raise ValueError('Real downloaded sample did not decrypt to a ZIP file')
    name_length = int.from_bytes(plaintext[26:28], 'little')
    name = bytes(plaintext[30:30 + name_length]).decode('utf-8', errors='replace')
    print(f'Live probe passed: downloaded {count:,} bytes and decrypted a ZIP header.', flush=True)
    return {'sample_bytes': count, 'resume_sample_bytes': len(resumed), 'resume_verified': True,
            'zip_header_verified': True, 'first_zip_entry': name,
            'full_archive_verified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Samsung FUS firmware download, resume and AES decryption')
    parser.add_argument('operation', choices=('probe', 'download', 'decrypt'))
    parser.add_argument('--model', help='Samsung model, for example SM-F9760')
    parser.add_argument('--region', help='CSC, for example CHC')
    parser.add_argument('--version', help='AP/CSC/CP[/DATA]; download/probe defaults to current FOTA version')
    parser.add_argument('--output-dir', type=Path, default=Path('downloads'))
    parser.add_argument('--input', type=Path, help='Encrypted file for decrypt')
    parser.add_argument('--output', type=Path, help='Destination ZIP for decrypt')
    parser.add_argument('--key-file', type=Path, help='Private key JSON previously saved with --save-key')
    parser.add_argument('--save-key', action='store_true', help='Save a private offline decryption key beside the download')
    parser.add_argument('--no-decrypt', action='store_true', help='Download and verify the encrypted file only')
    parser.add_argument('--transport', choices=('requests', 'curl'), default='requests')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / '.env')
    client = None
    try:
        settings = Settings.from_env(root)
        if args.operation == 'decrypt' and not args.input:
            raise ValueError('decrypt requires --input')
        if args.key_file and args.operation != 'decrypt':
            raise ValueError('--key-file is only supported for decrypt')
        if args.operation == 'decrypt' and args.key_file:
            private = load_json(args.key_file)
            key = bytes.fromhex(private['key'])
            metadata = private['metadata']
            if (not re.fullmatch(r'[A-Za-z0-9_.-]{1,255}\.enc[24]', metadata.get('filename', ''))
                    or type(metadata.get('size_bytes')) is not int or metadata['size_bytes'] <= 0):
                raise ValueError('Invalid private key file metadata')
            binary = Binary(metadata, key, private.get('crc32'))
        else:
            if not args.model or not args.region:
                raise ValueError('--model and --region are required')
            validate_device(args.model, args.region)
            if args.operation == 'decrypt' and not args.version:
                raise ValueError('Online decryption requires the file\'s exact --version (or --key-file)')
            client = DownloadClient(settings, args.transport)
            version = args.version or client.latest(args.model, args.region)
            binary = client.information(args.model, args.region, version)
            if args.version and full_version(binary.metadata['version']) != full_version(args.version):
                raise ValueError('FUS returned another version; rerun with that exact version or omit --version for latest')
        metadata = binary.metadata
        print(f"Firmware: {metadata['version']}\nFile: {metadata['filename']}\nSize: {metadata['size_bytes']:,} bytes", flush=True)
        directory = (args.output.parent if args.operation == 'decrypt' and args.output else args.output_dir).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        with run_lock(directory):
            if args.operation == 'probe':
                result = probe(client, binary, args.region)
            elif args.operation == 'download':
                if args.save_key and binary.key:
                    atomic_json(directory / (metadata['filename'] + '.key.private.json'),
                                {'key': binary.key.hex(), 'metadata': metadata, 'crc32': binary.crc32}, private=True)
                encrypted, result = download_file(client, binary, args.region, directory, settings.retries, Progress('Download'))
                result['encrypted_file'] = encrypted.name
                if not args.no_decrypt and binary.key:
                    destination = directory / metadata['filename'].rsplit('.', 1)[0]
                    result.update(decrypt_file(encrypted, destination, binary.key, Progress('Decrypt')))
                    result['decrypted_file'] = destination.name
            else:
                verify_file(args.input, metadata['size_bytes'], binary.crc32)
                destination = args.output or directory / metadata['filename'].rsplit('.', 1)[0]
                result = decrypt_file(args.input, destination, binary.key, Progress('Decrypt'))
                result['decrypted_file'] = destination.name
            report = {'model': args.model, 'region': args.region, 'operation': args.operation,
                      'version': metadata['version'], 'filename': metadata['filename'],
                      'checked_at': time.strftime('%Y-%m-%d %H:%M:%S%z'), **result}
            atomic_json(directory / (metadata['filename'] + f'.{args.operation}.json'), report)
        print(f'{args.operation} completed. Output directory: {directory}', flush=True)
        return 0
    except KeyboardInterrupt:
        print('Interrupted. Downloaded .part bytes are retained; run the same command to resume.')
        return 130
    except (ValueError, RuntimeError, FetchError) as error:
        print(f'Firmware operation failed: {error}', flush=True)
        return 1
    except requests.RequestException as error:
        print(f'Firmware connection failed: {type(error).__name__}', flush=True)
        return 1
    except Exception as error:
        print(f'Firmware operation failed: {type(error).__name__}', flush=True)
        return 1
    finally:
        if client:
            client.session.close()


if __name__ == '__main__':
    raise SystemExit(main())

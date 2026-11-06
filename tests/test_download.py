import base64
import hashlib
import io
import os
import unittest
import zipfile
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from samsung_firmware.download import (Binary, binary_details, build_init, check_response,
    decrypt_file, download_file, full_version, logic_check, main, parse_md5, probe, verify_file)
from samsung_firmware.files import atomic_json
from samsung_firmware.curl_transport import config_quote

VERSION = 'F9760ZCU3AZJ8/F9760CHC3AZJ8/F9760ZCU3AZI1/F9760ZCU3AZJ8'
FILENAME = 'SM-F9760_1_20261017001721_5i3kar5ixq_fac.zip.enc4'
KEY = bytes(range(16))


def fixture():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('AP_test.tar.md5', b'firmware-test-data' * 5000)
        archive.writestr('CSC_test.tar.md5', bytes(range(256)) * 300)
    plaintext = output.getvalue()
    padded = plaintext + b'\0' * (-len(plaintext) % 16)
    cipher = Cipher(algorithms.AES(KEY), modes.ECB()).encryptor()
    encrypted = cipher.update(padded) + cipher.finalize()
    metadata = {'filename': FILENAME, 'version': VERSION, 'size_bytes': len(encrypted),
                'url': 'https://cloud-neofussvr.samsungmobile.com/NF_SmartDownloadBinaryForMass.do?file=/test/' + FILENAME}
    return Binary(metadata, KEY, zlib.crc32(encrypted)), encrypted, plaintext


class Response:
    def __init__(self, body, start, end, total, status=206, disconnect=False):
        self.body = body
        self.status_code = status
        self.headers = {'Content-Range': f'bytes {start}-{end}/{total}', 'Content-Length': str(end - start + 1)}
        self.disconnect = disconnect

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        yield self.body[:32]
        if self.disconnect:
            raise requests.ConnectionError('temporary disconnect')
        yield self.body[32:]


class Client:
    protocol = 'smart'

    def __init__(self, payload, fail=False):
        self.payload = payload
        self.fail = fail
        self.offsets = []

    def initialize(self, binary, region):
        pass

    def request(self, endpoint):
        pass

    def stream(self, binary, start, end):
        self.offsets.append(start)
        fail = self.fail
        self.fail = False
        return Response(self.payload[start:end + 1], start, end, len(self.payload), disconnect=fail)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.binary, self.encrypted, self.plaintext = fixture()

    def test_complete_download_decrypt_and_zip_crc(self):
        path, checksums = download_file(Client(self.encrypted), self.binary, 'CHC', self.directory)
        self.assertEqual(checksums['sha256'], hashlib.sha256(self.encrypted).hexdigest())
        destination = self.directory / 'firmware.zip'
        result = decrypt_file(path, destination, KEY)
        self.assertEqual(result['zip_entries'], 2)
        self.assertTrue(result['zip_crc_verified'])
        self.assertEqual(destination.read_bytes()[:len(self.plaintext)], self.plaintext)
        with zipfile.ZipFile(destination) as archive:
            self.assertIsNone(archive.testzip())

    def test_retry_resumes_exact_saved_offset(self):
        client = Client(self.encrypted, fail=True)
        path, _ = download_file(client, self.binary, 'CHC', self.directory)
        self.assertEqual(client.offsets, [0, 32])
        self.assertEqual(path.read_bytes(), self.encrypted)

    def test_restart_resumes_after_exhausted_retries(self):
        with self.assertRaises(RuntimeError):
            download_file(Client(self.encrypted, fail=True), self.binary, 'CHC', self.directory, retries=0)
        partial = self.directory / (FILENAME + '.part')
        self.assertEqual(partial.stat().st_size, 32)
        client = Client(self.encrypted)
        path, _ = download_file(client, self.binary, 'CHC', self.directory)
        self.assertEqual(client.offsets, [32])
        self.assertEqual(path.read_bytes(), self.encrypted)

    def test_range_ignored_rejected_before_append(self):
        with self.assertRaises(RuntimeError):
            download_file(Client(self.encrypted, fail=True), self.binary, 'CHC', self.directory, retries=0)
        partial = self.directory / (FILENAME + '.part')
        client = Client(self.encrypted)
        with patch.object(client, 'stream', return_value=Response(self.encrypted, 0, len(self.encrypted)-1, len(self.encrypted), status=200)):
            with self.assertRaisesRegex(ValueError, 'range'):
                download_file(client, self.binary, 'CHC', self.directory)
        self.assertEqual(partial.stat().st_size, 32)

    def test_wrong_content_range_and_length(self):
        response = Response(b'', 10, 19, 100)
        with self.assertRaisesRegex(ValueError, 'Content-Range'):
            check_response(response, 0, 9, 100)
        response.headers['Content-Length'] = '11'
        with self.assertRaisesRegex(ValueError, 'Content-Length'):
            check_response(response, 10, 19, 100)
        self.assertEqual(parse_md5(base64.b64encode(KEY).decode()), KEY.hex())
        with self.assertRaises(ValueError):
            parse_md5('not-md5')

    def test_mismatched_manifest_and_oversized_partial_rejected(self):
        partial = self.directory / (FILENAME + '.part')
        partial.write_bytes(b'old-file')
        with self.assertRaisesRegex(ValueError, 'manifest'):
            download_file(Client(self.encrypted), self.binary, 'CHC', self.directory)
        identity = {**self.binary.metadata, 'crc32': self.binary.crc32}
        atomic_json(self.directory / (FILENAME + '.download.json'), {'identity': identity})
        partial.write_bytes(self.encrypted + b'X')
        with self.assertRaisesRegex(ValueError, 'larger'):
            download_file(Client(self.encrypted), self.binary, 'CHC', self.directory)

    def test_checksum_failure_does_not_publish_completed_file(self):
        self.binary.crc32 ^= 1
        with self.assertRaisesRegex(ValueError, 'CRC32'):
            download_file(Client(self.encrypted), self.binary, 'CHC', self.directory)
        self.assertFalse((self.directory / FILENAME).exists())
        self.assertTrue((self.directory / (FILENAME + '.part')).exists())

    def test_wrong_key_and_damaged_zip_do_not_publish_output(self):
        source = self.directory / 'firmware.enc4'
        destination = self.directory / 'firmware.zip'
        source.write_bytes(self.encrypted)
        with self.assertRaisesRegex(ValueError, 'ZIP header'):
            decrypt_file(source, destination, b'X' * 16)
        self.assertFalse(destination.exists())
        self.assertFalse(destination.with_name(destination.name + '.part').exists())
        damaged = bytearray(self.encrypted)
        damaged[100] ^= 1
        source.write_bytes(damaged)
        with self.assertRaises((ValueError, zipfile.BadZipFile, zlib.error)):
            decrypt_file(source, destination, KEY)
        self.assertFalse(destination.exists())
        source.write_bytes(self.encrypted[:-1])
        with self.assertRaisesRegex(ValueError, 'aligned'):
            decrypt_file(source, destination, KEY)

    def test_existing_output_not_overwritten(self):
        source = self.directory / 'firmware.enc4'
        source.write_bytes(self.encrypted)
        destination = self.directory / 'firmware.zip'
        destination.write_bytes(b'user-data')
        with self.assertRaisesRegex(ValueError, 'exists'):
            decrypt_file(source, destination, KEY)
        self.assertEqual(destination.read_bytes(), b'user-data')

    def test_real_probe_logic_with_fixture(self):
        report = probe(Client(self.encrypted), self.binary, 'CHC')
        self.assertTrue(report['zip_header_verified'])
        self.assertFalse(report['full_archive_verified'])
        self.assertEqual(report['first_zip_entry'], 'AP_test.tar.md5')

    def test_protocol_key_derivation_and_initialization(self):
        logic = '0123456789ABCDEF'
        xml = f'''<FUSMsg><FUSBody><Results><Status>S00</Status><BINARY_SW_VERSION><Data>{VERSION}</Data></BINARY_SW_VERSION></Results><Put>
          <BINARY_SW_VERSION><Data>{VERSION}</Data></BINARY_SW_VERSION>
          <BINARY_NAME><Data>{FILENAME}</Data></BINARY_NAME><MODEL_PATH><Data>/test/</Data></MODEL_PATH>
          <BINARY_BYTE_SIZE><Data>1024</Data></BINARY_BYTE_SIZE><BINARY_CRC><Data>1234</Data></BINARY_CRC>
          <DEVICE_MODEL_TYPE><Data>1</Data></DEVICE_MODEL_TYPE><LOGIC_VALUE_FACTORY><Data>{logic}</Data></LOGIC_VALUE_FACTORY>
          </Put></FUSBody></FUSMsg>'''.encode()
        binary = binary_details(xml, VERSION, 'SM-F9760', 'CHC')
        derived = hashlib.md5(''.join(VERSION[ord(c) & 15] for c in logic).encode()).digest()
        self.assertEqual(binary.key, derived)
        self.assertIn('?file=/test/', binary.metadata['url'])
        self.assertNotIn('%2F', binary.metadata['url'])
        self.assertNotIn(binary.key.hex(), repr(binary))
        request = ElementTree.fromstring(build_init(binary, 'CHC', logic))
        self.assertEqual(request.findtext('./FUSBody/Put/LOGIC_CHECK/Data'), logic_check(FILENAME[-25:-9], logic))
        self.assertEqual(request.findtext('./FUSBody/Put/DEVICE_MODEL_TYPE/Data'), '1')
        with self.assertRaisesRegex(ValueError, 'decryption parameters'):
            binary_details(xml.replace(logic.encode(), b''), VERSION, 'SM-F9760', 'CHC')
        enc2 = binary_details(xml.replace(b'.enc4', b'.enc2'), VERSION, 'SM-F9760', 'CHC')
        self.assertEqual(enc2.key, hashlib.md5(f'CHC:SM-F9760:{VERSION}'.encode()).digest())
        self.assertEqual(full_version('/'.join(VERSION.split('/')[:3])), VERSION)

    def test_offline_decrypt_command_and_private_key(self):
        source = self.directory / FILENAME
        source.write_bytes(self.encrypted)
        key_file = self.directory / 'key.private.json'
        atomic_json(key_file, {'key': KEY.hex(), 'metadata': self.binary.metadata,
                               'crc32': self.binary.crc32}, private=True)
        output = self.directory / 'offline.zip'
        with patch('samsung_firmware.download.DownloadClient', side_effect=AssertionError('offline decrypt made a network request')):
            self.assertEqual(main(['decrypt', '--input', str(source), '--key-file', str(key_file), '--output', str(output)]), 0)
        self.assertTrue(output.exists())
        reports = list(self.directory.glob('*.decrypt.json'))
        self.assertEqual(len(reports), 1)
        self.assertNotIn(KEY.hex(), reports[0].read_text())
        self.binary.metadata['filename'] = '../outside.zip.enc4'
        atomic_json(key_file, {'key': KEY.hex(), 'metadata': self.binary.metadata})
        self.assertEqual(main(['decrypt', '--input', str(source), '--key-file', str(key_file), '--output', str(output)]), 1)

    def test_already_complete_download_skips_network(self):
        download_file(Client(self.encrypted), self.binary, 'CHC', self.directory)
        client = Client(self.encrypted)
        path, _ = download_file(client, self.binary, 'CHC', self.directory)
        self.assertEqual(client.offsets, [])
        self.assertEqual(path.read_bytes(), self.encrypted)

    def test_disk_space_and_header_injection(self):
        with patch('samsung_firmware.download.shutil.disk_usage') as usage:
            usage.return_value.free = 0
            with self.assertRaisesRegex(ValueError, 'space'):
                download_file(Client(self.encrypted), self.binary, 'CHC', self.directory)
        with self.assertRaises(ValueError):
            config_quote('Authorization: test\r\nOther: bad')
        self.assertEqual(config_quote('a"b'), '"a\\"b"')


if __name__ == '__main__':
    unittest.main()

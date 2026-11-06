"""Optional streaming curl transport, using the same FUS protocol as requests."""
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import requests
from requests.structures import CaseInsensitiveDict


def config_quote(value):
    if '\r' in value or '\n' in value:
        raise ValueError('Invalid HTTP header')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


class CurlResponse:
    def __init__(self, executable, cookie_file, method, url, data, headers, timeout):
        self.temporary = tempfile.TemporaryDirectory(prefix='firmware-http-')
        directory = Path(self.temporary.name)
        self.header_file = directory / 'headers'
        self.error_file = (directory / 'error').open('w+b')
        self.process = None
        arguments = [executable, '--silent', '--show-error', '--globoff', '--proto', '=https',
                     '--connect-timeout', str(timeout[0]), '--speed-time', str(timeout[1]),
                     '--speed-limit', '1', '--max-redirs', '0', '--request', method,
                     '--dump-header', str(self.header_file), '--cookie', str(cookie_file),
                     '--cookie-jar', str(cookie_file), '--config', '-', url]
        if method == 'POST':
            body = directory / 'request'
            body.write_bytes(data)
            arguments.extend(['--data-binary', '@' + str(body), '--max-time', str(sum(timeout))])
        config = ''.join('header = ' + config_quote(f'{name}: {value}') + '\n' for name, value in headers.items())
        try:
            self.process = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self.error_file, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.process.stdin.write(config.encode('utf-8'))
            self.process.stdin.close()
            deadline = time.monotonic() + sum(timeout)
            while True:
                if self.header_file.exists():
                    raw = self.header_file.read_bytes().replace(b'\r\n', b'\n')
                    for block in reversed(raw.split(b'\n\n')[:-1]):
                        lines = block.decode('iso-8859-1').splitlines()
                        if not lines or not lines[0].startswith('HTTP/') or 'connection established' in lines[0].lower():
                            continue
                        status = int(lines[0].split()[1])
                        if status < 200:
                            continue
                        self.status_code = status
                        pairs = (line.split(':', 1) for line in lines[1:] if ':' in line)
                        self.headers = CaseInsensitiveDict((key, value.strip()) for key, value in pairs)
                        return
                if self.process.poll() is not None:
                    raise requests.ConnectionError(f'curl_exit_{self.process.returncode}')
                if time.monotonic() >= deadline:
                    raise requests.Timeout('curl_header_timeout')
                time.sleep(0.02)
        except BaseException:
            self.close()
            raise

    def iter_content(self, chunk_size):
        while True:
            block = self.process.stdout.read(chunk_size)
            if not block:
                break
            yield block
        status = self.process.wait(timeout=5)
        if status:
            raise requests.ConnectionError(f'curl_exit_{status}')

    def close(self):
        if self.process:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=5)
            for stream in (self.process.stdin, self.process.stdout):
                if stream and not stream.closed:
                    stream.close()
        self.error_file.close()
        self.temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class CurlSession:
    def __init__(self):
        self.executable = shutil.which('curl')
        if not self.executable:
            raise ValueError('curl is not installed or is not on PATH')
        self.temporary = tempfile.TemporaryDirectory(prefix='firmware-session-')
        self.cookie_file = Path(self.temporary.name) / 'cookies'

    def post(self, url, data=b'', headers=None, timeout=(10, 60), **kwargs):
        return CurlResponse(self.executable, self.cookie_file, 'POST', url, data, headers or {}, timeout)

    def get(self, url, headers=None, timeout=(10, 60), **kwargs):
        return CurlResponse(self.executable, self.cookie_file, 'GET', url, b'', headers or {}, timeout)

    def close(self):
        self.temporary.cleanup()

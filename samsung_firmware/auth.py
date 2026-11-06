import hashlib
import base64
import json
import os
import struct
import threading
import time
from pathlib import Path

import requests

from .network import FetchError
from .files import atomic_write


AUTH_COMMIT = "ed73a034d3b27d84c68b2c21622a9de6618455de"
AUTH_GIT_BLOB = "ce2d4b3713a24c5e795144433837d65e3b94855e"
AUTH_SHA256 = "6f969176bbe3eca193b51509313ca45d32366cf1825a907f04fde5b64d3f35b4"
AUTH_URL = f"https://raw.githubusercontent.com/zacharee/SamloaderKotlin/{AUTH_COMMIT}/common/src/commonMain/composeResources/files/auth_param.dat"
SHIFT_ORDER = (0, 5, 10, 15, 4, 9, 14, 3, 8, 13, 2, 7, 12, 1, 6, 11)
_lock = threading.Lock()
_blobs = {}
_failures = {}


def download_auth_blob(settings):
    urls = (AUTH_URL, f"https://api.github.com/repos/zacharee/SamloaderKotlin/git/blobs/{AUTH_GIT_BLOB}")
    for url in urls:
        try:
            with requests.get(url, timeout=settings.timeout, stream=True, allow_redirects=False) as response:
                if response.status_code != 200:
                    continue
                chunks = []
                size = 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise FetchError("auth_params_too_large")
                    chunks.append(chunk)
                body = b"".join(chunks)
                if url != AUTH_URL:
                    content = json.loads(body)["content"]
                    body = base64.b64decode("".join(content.split()), validate=True)
                return body
        except (requests.RequestException, ValueError, KeyError):
            continue
    raise FetchError("auth_params_download_failed")


def valid_blob(blob):
    override = os.getenv("FW_FUS_AUTH_SHA256", "")
    if override:
        return hashlib.sha256(blob).hexdigest() == override.lower()
    return hashlib.sha256(blob).hexdigest() == AUTH_SHA256


def load_auth_blob(settings):
    configured = os.getenv("FW_FUS_AUTH_FILE", "")
    path = Path(configured).expanduser() if configured else settings.cache_dir / "auth_param.dat"
    if not path.is_absolute():
        path = settings.root / path
    key = (str(path), os.getenv("FW_FUS_AUTH_SHA256", ""))
    with _lock:
        if key in _blobs:
            return _blobs[key]
        if time.time() - _failures.get(key, 0) < 300:
            raise FetchError("auth_params_download_failed")
        if path.is_file():
            if path.stat().st_size > 2 * 1024 * 1024:
                raise FetchError("auth_params_too_large")
            blob = path.read_bytes()
        elif configured:
            raise FetchError("auth_params_missing")
        else:
            try:
                blob = download_auth_blob(settings)
            except FetchError:
                _failures[key] = time.time()
                raise
        if not valid_blob(blob):
            raise FetchError("auth_params_checksum_failed")
        if not path.exists():
            atomic_write(path, blob, private=True)
        _blobs[key] = blob
        return blob


def authenticate(nonce, blob):
    if len(nonce) != 16 or len(blob) < 56:
        raise FetchError("invalid_auth_input")
    header = struct.unpack_from("<14I", blob)
    lookup_size, final_size, selector_size = header[3], header[5], header[11]
    tables = memoryview(blob)[56:]
    if len(tables) < lookup_size + final_size + selector_size + 9 * 4 * 6144:
        raise FetchError("invalid_auth_params")
    state = list(nonce.encode("ascii"))
    try:
        for round_index in range(9):
            shifted = [state[index] for index in SHIFT_ORDER]
            next_state = []
            for column in range(4):
                lanes = []
                for lane in range(4):
                    block = round_index * 16 + column * 4 + lane
                    lookup = tables[block * 4096 + shifted[column * 4 + lane] * 16:][:16]
                    selectors = tables[lookup_size + final_size + block * 32:][:32]
                    transformed = []
                    for output_byte in range(4):
                        value = 0
                        for bit in range(8):
                            selector = selectors[output_byte * 8 + bit]
                            source = lookup[selector >> 3] if selector >> 3 < 16 else 0
                            value = (value << 1) | ((source >> (7 - (selector & 7))) & 1)
                        transformed.append(value)
                    lanes.append(transformed)
                for output_byte in range(4):
                    offset = lookup_size + final_size + selector_size + (round_index * 4 + column) * 6144 + output_byte * 1536
                    table = tables[offset:offset + 1536]

                    def combine(left, right, bank):
                        high = (left & 240) | (right >> 4)
                        low = ((left & 15) << 4) | (right & 15)
                        return ((table[bank + high] << 4) ^ table[bank + 256 + low]) & 255

                    first = combine(lanes[0][output_byte], lanes[1][output_byte], 0)
                    second = combine(lanes[2][output_byte], lanes[3][output_byte], 512)
                    next_state.append(combine(first, second, 1024))
            state = next_state
        return bytes(tables[lookup_size + index * 256 + state[source]] for index, source in enumerate(SHIFT_ORDER)).hex()
    except (IndexError, ValueError):
        raise FetchError("invalid_auth_params") from None

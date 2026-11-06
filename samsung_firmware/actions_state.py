import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

import requests
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .files import atomic_write, run_lock


MAGIC = b"SAMSUNG-STATE-1\0"
ARCHIVE_FILE = "firmware.state.enc"
MAX_SIZE = 256 * 1024 * 1024
STATE_NAME = re.compile(r"(?:firmware\.sqlite3|telegram-outbox\.private\.json|cloud-pending-[a-f0-9]{16}\.private\.json)")


def state_context():
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    reference = os.environ.get("GITHUB_REF", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not reference.startswith("refs/heads/"):
        raise ValueError("State persistence requires a GitHub repository and branch")
    associated = f"{repository}\n{reference}".encode()
    name = "firmware-state-v1-" + hashlib.sha256(associated).hexdigest()[:24]
    secret = os.environ.get("FW_STATE_KEY", "").strip()
    if not re.fullmatch(r"[a-fA-F0-9]{64}", secret):
        raise ValueError("Set GitHub Actions secret FW_STATE_KEY to 64 random hexadecimal characters")
    return repository, reference.removeprefix("refs/heads/"), name, bytes.fromhex(secret), associated


def pack_state(data_dir, archive, key, associated):
    buffer = io.BytesIO()
    with run_lock(data_dir), tempfile.TemporaryDirectory() as temporary:
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(Path(data_dir).iterdir()):
                if not STATE_NAME.fullmatch(path.name):
                    continue
                if path.is_symlink() or not path.is_file():
                    raise ValueError("State files must be regular files")
                source = path
                if path.name == "firmware.sqlite3":
                    source = Path(temporary) / path.name
                    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as database:
                        with closing(sqlite3.connect(source)) as backup:
                            database.backup(backup)
                if source.stat().st_size + sum(entry.file_size for entry in bundle.infolist()) > MAX_SIZE:
                    raise ValueError("State exceeds the 256 MiB limit")
                bundle.write(source, path.name)
        plaintext = buffer.getvalue()
        if len(plaintext) + len(MAGIC) + 28 > MAX_SIZE:
            raise ValueError("Encrypted state exceeds the size limit")
        nonce = os.urandom(12)
        ciphertext = MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, associated)
        atomic_write(archive, ciphertext, private=True)


def unpack_state(payload, data_dir, key, associated):
    if len(payload) > MAX_SIZE or not payload.startswith(MAGIC):
        raise ValueError("Invalid encrypted state archive")
    nonce = payload[len(MAGIC):len(MAGIC) + 12]
    try:
        plaintext = AESGCM(key).decrypt(nonce, payload[len(MAGIC) + 12:], associated)
    except (InvalidTag, ValueError):
        raise ValueError("State authentication failed; check FW_STATE_KEY and the repository/branch") from None
    records = {}
    with zipfile.ZipFile(io.BytesIO(plaintext)) as bundle:
        entries = bundle.infolist()
        if sum(entry.file_size for entry in entries) > MAX_SIZE:
            raise ValueError("Expanded state exceeds the size limit")
        for entry in entries:
            if not STATE_NAME.fullmatch(entry.filename) or entry.filename in records:
                raise ValueError("Unexpected or duplicate file in state archive")
            content = bundle.read(entry)
            if entry.filename.endswith(".json"):
                if not isinstance(json.loads(content), dict):
                    raise ValueError("Invalid state queue")
            elif not content.startswith(b"SQLite format 3\0"):
                raise ValueError("Invalid SQLite state")
            records[entry.filename] = content
    with run_lock(data_dir):
        for filename, content in records.items():
            destination = Path(data_dir) / filename
            if destination.is_symlink():
                raise ValueError("Refusing to restore over a symlink")
            if filename == "firmware.sqlite3" and any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm")):
                raise ValueError("Refusing to replace a database with active WAL files")
            atomic_write(destination, content, private=True)
    return len(records)


def read_limited(response):
    payload = bytearray()
    for chunk in response.iter_content(65536):
        payload.extend(chunk)
        if len(payload) > MAX_SIZE:
            raise ValueError("Artifact exceeds the size limit")
    return bytes(payload)


def restore_state(data_dir, repository, branch, name, key, associated):
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise ValueError("GitHub artifact restore requires GITHUB_TOKEN with actions:read")
    api = f"https://api.github.com/repos/{repository}/actions/artifacts"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    selected = None
    page = 1
    with requests.Session() as session:
        while True:
            with session.get(api, params={"name": name, "per_page": 100, "page": page}, headers=headers,
                             timeout=(10, 60), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"Artifact listing failed: HTTP {response.status_code}")
                result = response.json()
            for artifact in result.get("artifacts", []):
                run = artifact.get("workflow_run") or {}
                if (artifact.get("name") == name and not artifact.get("expired")
                        and run.get("head_branch") == branch and str(run.get("id")) != os.environ.get("GITHUB_RUN_ID")):
                    order = (artifact.get("created_at", ""), int(artifact["id"]))
                    if selected is None or order > (selected.get("created_at", ""), int(selected["id"])):
                        selected = artifact
            if page * 100 >= result.get("total_count", 0):
                break
            page += 1
        if selected is None:
            print("No previous encrypted state; starting from repository JSON.")
            return
        if selected.get("size_in_bytes", 0) > MAX_SIZE:
            raise ValueError("Artifact exceeds the size limit")
        location = None
        with session.get(f"{api}/{int(selected['id'])}/zip", headers=headers,
                         timeout=(10, 60), allow_redirects=False, stream=True) as response:
            if response.status_code == 302:
                location = response.headers.get("Location", "")
            elif response.status_code == 200:
                payload = read_limited(response)
            else:
                raise RuntimeError(f"Artifact download failed: HTTP {response.status_code}")
        if location is not None:
            parsed = urlsplit(location)
            if (parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username or parsed.password
                    or not (parsed.hostname or "").endswith((".blob.core.windows.net", ".githubusercontent.com"))):
                raise ValueError("Unexpected GitHub artifact download host")
            with requests.get(location, timeout=(10, 60), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"Artifact storage failed: HTTP {response.status_code}")
                payload = read_limited(response)
    with zipfile.ZipFile(io.BytesIO(payload)) as wrapper:
        entry = wrapper.getinfo(ARCHIVE_FILE)
        if entry.file_size > MAX_SIZE:
            raise ValueError("Encrypted state exceeds the size limit")
        encrypted = wrapper.read(entry)
    count = unpack_state(encrypted, data_dir, key, associated)
    print(f"Restored {count} state files from encrypted artifact {int(selected['id'])}.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Encrypted GitHub Actions firmware state")
    parser.add_argument("operation", choices=("restore", "pack"))
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args(argv)
    try:
        repository, branch, name, key, associated = state_context()
        root = Path(__file__).resolve().parent.parent
        data_dir = Path(os.environ.get("FW_DATA_DIR", "data"))
        if not data_dir.is_absolute():
            data_dir = root / data_dir
        if args.operation == "restore":
            restore_state(data_dir, repository, branch, name, key, associated)
        elif args.archive is None:
            raise ValueError("pack requires --archive")
        else:
            pack_state(data_dir, args.archive, key, associated)
            print("Encrypted database and notification/sync queues for artifact upload.")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"artifact_name={name}\n")
        return 0
    except (ValueError, RuntimeError) as error:
        print(f"State persistence failed: {error}")
    except Exception as error:
        print(f"State persistence failed: {type(error).__name__}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

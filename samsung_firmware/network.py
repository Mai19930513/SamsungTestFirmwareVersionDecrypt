import hashlib
import re
import threading
import time
from urllib.parse import urlsplit

import requests
from lxml import etree
from requests.adapters import HTTPAdapter
from urllib3.util import Retry, SKIP_HEADER

from .files import atomic_json, load_json


FOTA_ROOT = "https://fota-cloud-dn.ospserver.net/firmware"
PUBLIC_HOSTS = {"fota-cloud-dn.ospserver.net", "doc.samsungmobile.com"}


class FetchError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_device(model, region):
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{2,31}", model):
        raise ValueError("Invalid model code")
    if not re.fullmatch(r"[A-Z0-9]{3}", region):
        raise ValueError("Invalid region code")


def parse_xml(content):
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise FetchError("unsafe_xml")
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        return etree.fromstring(content, parser=parser)
    except (etree.XMLSyntaxError, ValueError):
        raise FetchError("invalid_xml") from None


def valid_version(value):
    return bool(re.fullmatch(r"[A-Z0-9]{8,32}/[A-Z0-9]{8,32}/[A-Z0-9]{0,32}(?:/[A-Z0-9]{0,32})?", value or ""))


def parse_stable(content):
    root = parse_xml(content)
    if root.tag != "versioninfo" or root.find(".//firmware/version") is None:
        raise FetchError("invalid_fota_document")
    latest = root.find(".//latest")
    version = (latest.text or "").strip() if latest is not None else ""
    if version and not valid_version(version):
        raise FetchError("invalid_version")
    history = [text.strip() for text in root.xpath(".//upgrade/value/text()") if valid_version(text.strip())]
    return {"version": version, "android": latest.get("o", "未知") if latest is not None else "未知", "history": history}


def parse_test(content):
    root = parse_xml(content)
    if root.tag != "versioninfo" or root.find(".//firmware/version/upgrade") is None:
        raise FetchError("invalid_fota_document")
    values = {value.strip().lower() for value in root.xpath(".//upgrade/value/text()") if value.strip()}
    if any(not re.fullmatch(r"(?:[a-f0-9]{32}|[a-f0-9]{64})", value) for value in values):
        raise FetchError("unknown_hash_format")
    return sorted(values)


class HttpClient:
    def __init__(self, settings):
        self.settings = settings
        self.local = threading.local()
        self.sessions = []
        self.session_lock = threading.Lock()

    def session(self):
        if not hasattr(self.local, "session"):
            session = requests.Session()
            retry = Retry(total=self.settings.retries, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504), allowed_methods={"GET"}, respect_retry_after_header=False)
            adapter = HTTPAdapter(max_retries=retry, pool_connections=self.settings.io_workers, pool_maxsize=self.settings.io_workers)
            session.mount("https://", adapter)
            self.local.session = session
            with self.session_lock:
                self.sessions.append(session)
        return self.local.session

    def get(self, url, force=False):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in PUBLIC_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise FetchError("untrusted_url")
        cache_path = self.settings.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        try:
            cached = load_json(cache_path)
        except (ValueError, OSError):
            cached = {}
        if not force and cached.get("body") and time.time() - cached.get("fetched_at", 0) < self.settings.cache_seconds:
            return cached["body"].encode("utf-8")
        headers = {"User-Agent": SKIP_HEADER}
        if cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached.get("last_modified"):
            headers["If-Modified-Since"] = cached["last_modified"]
        try:
            with self.session().get(url, headers=headers, timeout=self.settings.timeout, allow_redirects=False, stream=True) as response:
                if response.status_code == 304 and cached.get("body"):
                    cached["fetched_at"] = time.time()
                    atomic_json(cache_path, cached, private=True)
                    return cached["body"].encode("utf-8")
                if response.status_code != 200:
                    raise FetchError(f"http_{response.status_code}")
                chunks = []
                size = 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 8 * 1024 * 1024:
                        raise FetchError("response_too_large")
                    chunks.append(chunk)
                body = b"".join(chunks)
                try:
                    text = body.decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise FetchError("invalid_encoding") from None
                atomic_json(cache_path, {"body": text, "etag": response.headers.get("ETag"), "last_modified": response.headers.get("Last-Modified"), "fetched_at": time.time()}, private=True)
                return body
        except requests.RequestException as error:
            raise FetchError(self._error_code(error)) from None

    @staticmethod
    def _error_code(error):
        current = error
        seen = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, requests.exceptions.ProxyError):
                return "proxy_error"
            if isinstance(current, requests.exceptions.SSLError):
                return "tls_error"
            if isinstance(current, requests.exceptions.ConnectTimeout):
                return "connect_timeout"
            if isinstance(current, requests.exceptions.ReadTimeout):
                return "read_timeout"
            if isinstance(current, requests.exceptions.ConnectionError):
                return "connection_error"
            current = getattr(current, "__cause__", None) or getattr(current, "reason", None)
        return type(error).__name__

    def close(self):
        for session in self.sessions:
            session.close()

    def fetch_device(self, model, region, force=False):
        validate_device(model, region)
        result = {"model": model, "region": region, "errors": {}}
        for name, filename, parser in (("stable", "version.xml", parse_stable), ("test", "version.test.xml", parse_test)):
            try:
                result[name] = parser(self.get(f"{FOTA_ROOT}/{region}/{model}/{filename}", force))
            except FetchError as error:
                result["errors"][name] = error.code
        return result

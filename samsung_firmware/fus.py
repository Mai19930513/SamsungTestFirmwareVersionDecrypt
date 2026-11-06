import os
import re
import time
from urllib.parse import urlencode, urlsplit, parse_qs
from xml.etree import ElementTree
from pathlib import Path

import requests
from .auth import authenticate, load_auth_blob
from .files import load_json, now_text
from .network import FetchError, parse_xml, valid_version, validate_device


FUS_ROOT = "https://neofussvr.sslcs.cdngc.net/"
DOWNLOAD_HOST = "https://cloud-neofussvr.samsungmobile.com/"


def build_request(version, model, region, nonce, protocol="smart", identity=""):
    if len(version) < 16:
        raise ValueError("Invalid firmware version")
    message = ElementTree.Element("FUSMsg")
    header = ElementTree.SubElement(message, "FUSHdr")
    ElementTree.SubElement(header, "ProtoVer").text = "1" if protocol == "smart" else "1.0"
    ElementTree.SubElement(header, "SessionID").text = "0"
    ElementTree.SubElement(header, "MsgID").text = "1"
    body = ElementTree.SubElement(message, "FUSBody")
    put = ElementTree.SubElement(body, "Put")
    fields = {
        "ACCESS_MODE": "1" if protocol == "smart" else "2",
        "BINARY_NATURE": "1",
        "CLIENT_PRODUCT": "Smart Switch",
        "DEVICE_FW_VERSION": version,
        "DEVICE_LOCAL_CODE": region,
        "DEVICE_MODEL_NAME": model,
        "LOGIC_CHECK": "".join(version[ord(char) & 15] for char in nonce),
    }
    if protocol == "smart":
        fields = {key: value for key, value in fields.items() if key in {"ACCESS_MODE", "BINARY_NATURE", "LOGIC_CHECK"}}
        fields.update({"REQUEST_TYPE": "2", "BINARY_SW_VERSION": version, "DEVICE_SN_NUMBER": "",
                       "BINARY_LOCAL_CODE": region, "BINARY_MODEL_NAME": model})
        ElementTree.SubElement(put, "CmdID").text = "1"
    else:
        ap, csc, phone, content = version.split("/")
        fields.update({"CLIENT_VERSION": "4.3.23123_1", "DEVICE_IMEI_PUSH": identity,
                       "DEVICE_AID_CODE": region, "DEVICE_CONTENTS_DATA_VERSION": content,
                       "DEVICE_CSC_CODE2_VERSION": csc, "DEVICE_PDA_CODE1_VERSION": ap,
                       "DEVICE_PHONE_FONT_VERSION": phone})
    if region == "EUX":
        fields.update({"DEVICE_CC_CODE": "DE", "MCC_NUM": "262", "MNC_NUM": "01"})
    if region == "EUY":
        fields.update({"DEVICE_CC_CODE": "RS", "MCC_NUM": "220", "MNC_NUM": "01"})
    for name, value in fields.items():
        ElementTree.SubElement(ElementTree.SubElement(put, name), "Data").text = value
    get = ElementTree.SubElement(body, "Get")
    ElementTree.SubElement(get, "CmdID").text = "2"
    ElementTree.SubElement(get, "BINARY_SW_VERSION" if protocol == "smart" else "LATEST_FW_VERSION")
    return ElementTree.tostring(message, encoding="utf-8")


def parse_binary(content, requested_version, protocol="smart"):
    root = parse_xml(content)
    status = root.findtext("./FUSBody/Results/Status", "")
    if status not in {"200", "S00"}:
        safe_status = status if re.fullmatch(r"[A-Z0-9]{1,8}", status) else "invalid"
        raise FetchError(f"fus_{safe_status}")

    def field(name):
        return root.findtext(f"./FUSBody/Put/{name}/Data", "").strip()

    filename = field("BINARY_NAME")
    path = field("MODEL_PATH")
    version = field("LATEST_FW_VERSION") or field("BINARY_SW_VERSION") or requested_version
    size = field("BINARY_BYTE_SIZE")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", filename) or not filename.endswith((".enc2", ".enc4", ".zip")):
        raise FetchError("invalid_binary_name")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]{1,512}", path) or ".." in path or not path.endswith("/"):
        raise FetchError("invalid_binary_path")
    if not size.isdecimal() or int(size) <= 0 or not valid_version(version):
        raise FetchError("invalid_binary_metadata")
    return {
        "status": "available",
        "source": "samsung_fus",
        "version": version,
        "requested_version": requested_version,
        "url": DOWNLOAD_HOST + ("NF_SmartDownloadBinaryForMass.do" if protocol == "smart" else "NF_DownloadBinaryForMass.do") + "?" + urlencode({"file": path + filename}, safe="/"),
        "protocol": protocol,
        "filename": filename,
        "size_bytes": int(size),
        "encrypted": filename.endswith((".enc2", ".enc4")),
        "requires_auth": True,
        "direct_download": False,
        "checked_at": now_text(),
        "checked_epoch": time.time(),
        "error": None,
    }


def empty_download(status, version="", error=None):
    return {"status": status, "source": "samsung_fus", "version": version,
            "requested_version": version, "url": None, "filename": None,
            "size_bytes": None, "encrypted": None, "requires_auth": True,
            "direct_download": False, "checked_at": None if status in {"disabled", "not_checked"} else now_text(),
            "checked_epoch": 0 if status in {"disabled", "not_checked"} else time.time(), "error": error}


def public_download(value):
    if not isinstance(value, dict):
        return empty_download("not_checked")
    allowed = {"status", "source", "version", "requested_version", "url", "filename", "size_bytes",
               "encrypted", "requires_auth", "direct_download", "checked_at", "checked_epoch", "error", "protocol", "last_attempt_at"}
    result = {key: item for key, item in value.items() if key in allowed}
    result.setdefault("status", "not_checked")
    result.setdefault("url", None)
    if result["url"]:
        try:
            parsed = urlsplit(result["url"])
            query = parse_qs(parsed.query, strict_parsing=True)
            valid = (parsed.scheme == "https" and parsed.hostname == "cloud-neofussvr.samsungmobile.com"
                     and parsed.port in (None, 443) and not parsed.username and not parsed.password and not parsed.fragment
                     and parsed.path in {"/NF_SmartDownloadBinaryForMass.do", "/NF_DownloadBinaryForMass.do"}
                     and set(query) == {"file"} and len(query["file"]) == 1
                     and re.fullmatch(r"/[A-Za-z0-9_./-]+", query["file"][0]) and ".." not in query["file"][0])
        except (ValueError, TypeError):
            valid = False
        if not valid:
            return empty_download("error", error="unsafe_download_url")
    return result


class FusClient:
    def __init__(self, settings):
        self.settings = settings
        self.session = requests.Session()
        self.nonce = ""
        self.signature = ""
        self.protocol = os.getenv("FW_FUS_PROTOCOL", "smart")
        if self.protocol not in {"smart", "legacy"}:
            raise ValueError("FW_FUS_PROTOCOL must be smart or legacy")

    def request(self, endpoint, body=b""):
        authorization = f'FUS nonce="{self.nonce}", signature="{self.signature}", nc="", type="", realm=""'
        try:
            with self.session.post(FUS_ROOT + endpoint, data=body,
                                   headers={"Authorization": authorization, "User-Agent": "SMART 2.0" if self.protocol == "smart" else "Kiss2.0_FUS"},
                                   timeout=self.settings.timeout, allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise FetchError(f"http_{response.status_code}")
                if response.headers.get("NONCE"):
                    try:
                        nonce = response.headers["NONCE"][:16]
                        signature = authenticate(nonce, load_auth_blob(self.settings))
                        self.nonce, self.signature = nonce, signature
                    except (ValueError, UnicodeError):
                        raise FetchError("invalid_nonce") from None
                chunks = []
                size = 0
                for chunk in response.iter_content(16384):
                    size += len(chunk)
                    if size > 1024 * 1024:
                        raise FetchError("response_too_large")
                    chunks.append(chunk)
                return b"".join(chunks)
        except requests.RequestException as error:
            raise FetchError(type(error).__name__.lower()) from None

    def lookup(self, model, region, version):
        validate_device(model, region)
        if not valid_version(version):
            return empty_download("unavailable", version, "no_stable_version")
        parts = version.split("/")
        if len(parts) == 3:
            parts.append(parts[0])
        if not parts[2]:
            parts[2] = parts[0]
        fus_version = "/".join(parts)
        try:
            identity = ""
            identities_path = os.getenv("FW_DEVICE_IDENTITIES_FILE", "")
            if identities_path and self.protocol == "legacy":
                path = Path(identities_path).expanduser()
                if not path.is_absolute():
                    path = self.settings.root / path
                try:
                    identity = load_json(path).get(f"{model}/{region}", "")
                except (ValueError, OSError):
                    raise FetchError("invalid_identity_file") from None
                if identity and (not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9]{8,32}", identity)):
                    raise FetchError("invalid_device_identity")
            prefix = "NF_SmartDownload" if self.protocol == "smart" else "NF_Download"
            self.request(prefix + "GenerateNonce.do")
            if not self.nonce:
                raise FetchError("missing_nonce")
            body = build_request(fus_version, model, region, self.nonce, self.protocol, identity)
            return parse_binary(self.request(prefix + "BinaryInform.do", body), version, self.protocol)
        except FetchError as error:
            status = "unavailable" if error.code.startswith("fus_") else "error"
            return empty_download(status, version, error.code)
        finally:
            self.session.close()


def get_download(settings, model, region, version, previous=None, force=False):
    previous = previous or {}
    if not settings.downloads:
        return empty_download("disabled", version)
    protocol = os.getenv("FW_FUS_PROTOCOL", "smart")
    if (not force and previous.get("requested_version") == version
            and previous.get("protocol", "smart") == protocol
            and previous.get("status") == "available"
            and time.time() - previous.get("checked_epoch", 0) < settings.download_cache_hours * 3600):
        return previous
    result = FusClient(settings).lookup(model, region, version)
    if result["status"] == "error" and previous.get("url") and previous.get("requested_version") == version:
        return {**previous, "status": "stale", "error": result["error"], "last_attempt_at": result["checked_at"]}
    return result

"""Samsung AP suffix: update type, binary revision, OS branch, year, month, build.

Z is a beta branch marker, not an Android release or a greater OS generation.
FUS may append a fourth (data) component or fill a missing CP with AP.
"""
import re
from dataclasses import dataclass

from .network import valid_version


@dataclass(frozen=True)
class Build:
    device: str
    market: str
    update_type: str
    bootloader: int
    branch: str
    year: int
    month: int
    revision: int

    @property
    def date_key(self):
        return self.year, self.month, self.revision

    @property
    def key(self):
        return (self.bootloader, *self.date_key)


def parse_build(version):
    if not isinstance(version, str):
        return None
    ap = version.split("/")[0]
    match = re.fullmatch(r"([A-Z0-9]{3,24})([A-Z]{2})([US])([0-9A-Z])([A-Z])([A-Z])([A-L])([0-9A-Z])", ap)
    if not match:
        return None
    device, market, kind, boot, branch, year, month, revision = match.groups()
    return Build(device, market, kind, int(boot, 36), branch,
                 2001 + ord(year) - ord("A"), ord(month) - ord("A") + 1, int(revision, 36))


def version_key(version):
    build = parse_build(version)
    return build.key if build else (-1,) * 4


def normalize_version(version):
    if not valid_version(version):
        return ""
    ap, csc, cp = version.split("/")[:3]
    return "/".join((ap, csc, cp))


def same_release(left, right):
    left, right = normalize_version(left), normalize_version(right)
    if not left or not right:
        return False
    a, b = left.split("/"), right.split("/")
    # Absent CP is unknown/not applicable; filling it is not a new release.
    return a[:2] == b[:2] and (a[2] == b[2] or not a[2] or not b[2])


def newer_test(version, stable):
    candidate, baseline = parse_build(version), parse_build(stable)
    if not candidate or not baseline or same_release(version, stable):
        return False
    if candidate.device != baseline.device:
        return False
    if candidate.bootloader < baseline.bootloader or candidate.date_key < baseline.date_key:
        return False
    if candidate.branch != "Z" and baseline.branch != "Z" and candidate.branch < baseline.branch:
        return False
    # U/S, market prefixes, and the beta marker do not establish chronology.
    return candidate.key > baseline.key


def release_baseline(record):
    versions = [record.get(field, "") for field in ("最新服务器正式版", "最新正式版")]
    return max((value for value in versions if parse_build(value)), key=version_key, default="")


def test_candidates(record):
    stable = release_baseline(record)
    current = record.get("当前服务器版本号", {})
    return sorted({version for version in current.values() if newer_test(version, stable)},
                  key=lambda value: (version_key(value), value), reverse=True)

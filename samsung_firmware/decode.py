import hashlib
import hmac
import itertools
import json
import math
import time
from datetime import datetime

from .network import valid_version
from .versions import version_key


ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
BOOTSTRAP = {"CHC": ("ZC", "CHC", "ZC"), "CHN": ("ZC", "CHC", ""),
             "TGY": ("ZH", "OZS", "ZC"), "KOO": ("KS", "OKR", "KS"),
             "EUX": ("XX", "OXM", "XX"), "INS": ("XX", "ODM", "XX"),
             "XAA": ("UE", "OYM", "UE"), "ATT": ("SQ", "OYN", "SQ")}



def target_hashes(values, key):
    marker = hmac.digest(key.encode("ascii"), b"all", "sha256").hex()
    markers = {marker, hashlib.md5(b"all", usedforsecurity=False).hexdigest()}
    return set(values) - markers, sorted(set(values) & markers)


def verified_versions(known, key):
    result = {}
    for digest, version in known.items():
        if not valid_version(version):
            continue
        payload = version.encode("ascii")
        actual = hashlib.md5(payload, usedforsecurity=False).hexdigest() if len(digest) == 32 else hmac.digest(key.encode("ascii"), payload, "sha256").hex()
        if actual == digest.lower():
            result[digest.lower()] = version
    return result


def search_ranges(stable, known, settings):
    versions = [version for version in [stable, *known] if valid_version(version)]
    current_year = min(2026, datetime.now().year)
    if versions:
        boot_values = [ALPHABET.index(version.split("/")[0][-5]) for version in versions]
        latest = stable if valid_version(stable) else max(versions, key=version_key)
        latest_ap = latest.split("/")[0]
        upper_boot = min(35, ALPHABET.index(latest_ap[-5]) + 1)
        bootloaders = ALPHABET[max(0, min(boot_values) - 1):upper_boot + 1]
        updates = [version.split("/")[0][-4] for version in versions if version.split("/")[0][-4] != "Z"]
        first_update = min(updates, default="A")
        last_update = ALPHABET[min(35, ALPHABET.index(latest_ap[-4]) + 1)]
        update_codes = ALPHABET[ALPHABET.index(first_update):ALPHABET.index(last_update) + 1] + "Z"
    else:
        bootloaders, update_codes = "012", "ABZ"
    first_year = max(2001, current_year - settings.history_years)
    years = "".join(chr(65 + year - 2001) for year in range(first_year, current_year + 1))
    return (settings.bootloader_codes or bootloaders,
            settings.update_codes or "".join(dict.fromkeys(update_codes)),
            settings.year_codes or years)


def indexed_product(axes, start=0):
    """Jump directly to a product offset, without replaying earlier groups."""
    sizes = tuple(map(len, axes))
    if start >= math.prod(sizes):
        return
    offsets = []
    index = start
    for size in reversed(sizes):
        index, offset = divmod(index, size)
        offsets.append(offset)
    offsets.reverse()
    first = tuple(axis[offset] for axis, offset in zip(axes, offsets))
    yield first
    for pivot in reversed(range(len(axes))):
        remainder = (axes[pivot][offsets[pivot] + 1:], *axes[pivot + 1:])
        for suffix in itertools.product(*remainder):
            yield first[:pivot] + suffix


def candidate_groups(model, region, stable, known, settings, start_group=0):
    versions = sorted({version for version in [stable, *known] if valid_version(version)}, key=lambda version: (version_key(version), version), reverse=True)
    template = stable if valid_version(stable) else (versions[0] if versions else "")
    if template:
        ap, csc, cp = template.split("/")[:3]
        prefixes = ap[:-6], csc[:-5], cp[:-6] if cp else ""
    elif region in BOOTSTRAP:
        base = model.removeprefix("SM-")
        prefixes = tuple(base + suffix if suffix else "" for suffix in BOOTSTRAP[region])
    else:
        return
    ap_prefix, csc_prefix, cp_prefix = prefixes
    modems = list(dict.fromkeys(version.split("/")[2] for version in versions))[:settings.modem_limit]
    bootloaders, updates, years = search_ranges(stable, known, settings)
    axes = (years[::-1], settings.month_codes[::-1], bootloaders[::-1],
            updates[::-1], "US", settings.serial_codes[::-1])
    for year, month, bootloader, update, kind, serial in indexed_product(axes, start_group):
        suffix = bootloader + update + year + month + serial
        prefix = f"{ap_prefix}{kind}{suffix}/{csc_prefix}{suffix}/"
        if not cp_prefix:
            yield prefix, ("",)
            continue
        same_modem = cp_prefix + kind + suffix
        previous = ALPHABET[max(0, ALPHABET.index(serial) - 2):ALPHABET.index(serial)]
        recent = [cp_prefix + kind + bootloader + update + year + month + value for value in dict.fromkeys("12" + previous)]
        candidates = [same_modem, *recent, *modems]
        if update == "Z":
            for modem_update in updates:
                if modem_update != "Z":
                    candidates.append(cp_prefix + kind + bootloader + modem_update + year + month + serial)
        yield prefix, tuple(dict.fromkeys(candidates))


def search_signature(model, region, stable, targets, known, settings):
    payload = [4, model, region, stable, sorted(targets), sorted(set(known.values())),
               search_ranges(stable, known.values(), settings), settings.month_codes,
               settings.serial_codes, settings.modem_limit, settings.hmac_key]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True).encode()).hexdigest()


def decode_device(model, region, stable, hashes, known, settings, metadata_only=False, progress=None):
    started = time.perf_counter()
    targets, markers = target_hashes(hashes, settings.hmac_key)
    verified = verified_versions(known, settings.hmac_key)
    recovered = verified_versions(progress.get("versions", {}), settings.hmac_key) if progress and not metadata_only else {}
    verified.update(recovered)
    plan_known = sorted(set(verified.values()))
    signature = search_signature(model, region, stable, targets, verified, settings)
    group_offset = modem_offset = previous_candidates = 0
    resumed = False
    if progress and not metadata_only:
        checkpoint = progress.get("progress", {})
        baseline = checkpoint.get("known", [])
        expected = search_signature(model, region, stable, targets, dict(enumerate(baseline)), settings)
        # Freeze modem order/ranges across discoveries; unrelated input changes restart.
        allowed = set(baseline) | set(recovered.values())
        if (checkpoint.get("signature") == expected and set(plan_known) <= allowed
                and set(baseline) <= set(plan_known) | set(recovered.values())):
            cursor = checkpoint.get("cursor", {})
            group_offset, modem_offset = cursor.get("group", 0), cursor.get("modem", 0)
            previous_candidates = checkpoint.get("candidates_total", 0)
            if any(type(value) is not int or value < 0 for value in (group_offset, modem_offset, previous_candidates)):
                raise ValueError("Invalid search checkpoint")
            plan_known, signature = baseline, expected
            resumed = True
    found = {}
    pending_md5 = {bytes.fromhex(value) for value in targets if len(value) == 32}
    pending_hmac = {bytes.fromhex(value) for value in targets if len(value) == 64}
    key_bytes = settings.hmac_key.encode("ascii")
    hmac_base = hmac.new(key_bytes, digestmod="sha256")

    def check_known(version):
        payload = version.encode("ascii")
        for pending, digest in (
            (pending_md5, hashlib.md5(payload, usedforsecurity=False).digest() if pending_md5 else None),
            (pending_hmac, hmac.digest(key_bytes, payload, "sha256") if pending_hmac else None),
        ):
            if digest in pending:
                found[digest.hex()] = version
                pending.remove(digest)

    for version in dict.fromkeys([*verified.values(), stable]):
        if valid_version(version):
            check_known(version)
    candidates = 0
    next_group, next_modem = group_offset, modem_offset
    state = "metadata_only" if metadata_only else "exhausted"
    if resumed and progress["stats"]["status"] in {"matched", "exhausted"}:
        state = "cached"
    elif not pending_md5 and not pending_hmac:
        state = "matched"
    elif not metadata_only:
        groups = candidate_groups(model, region, stable, plan_known, settings, group_offset)
        for group_index, (prefix, modems) in enumerate(groups, group_offset):
            payload = prefix.encode("ascii")
            md5_prefix = hashlib.md5(payload, usedforsecurity=False) if pending_md5 else None
            hmac_prefix = hmac_base.copy() if pending_hmac else None
            if hmac_prefix is not None:
                hmac_prefix.update(payload)
            start_modem = modem_offset if group_index == group_offset else 0
            for modem_index in range(start_modem, len(modems)):
                next_group, next_modem = group_index, modem_index
                if candidates >= settings.max_candidates or (candidates & 255 == 0 and time.perf_counter() - started >= settings.max_seconds):
                    state = "bounded"
                    break
                candidates += 1
                modem = modems[modem_index]
                modem_bytes = modem.encode("ascii")
                if pending_md5:
                    digest_state = md5_prefix.copy()
                    digest_state.update(modem_bytes)
                    digest = digest_state.digest()
                    if digest in pending_md5:
                        found[digest.hex()] = prefix + modem
                        pending_md5.remove(digest)
                if pending_hmac:
                    digest_state = hmac_prefix.copy()
                    digest_state.update(modem_bytes)
                    digest = digest_state.digest()
                    if digest in pending_hmac:
                        found[digest.hex()] = prefix + modem
                        pending_hmac.remove(digest)
                next_group, next_modem = group_index, modem_index + 1
                if not pending_md5 and not pending_hmac:
                    state = "matched"
                    break
            if state in {"matched", "bounded"}:
                break
            next_group, next_modem = group_index + 1, 0
    verified.update(found)
    unresolved = sorted(digest.hex() for digest in pending_md5 | pending_hmac)
    seconds = time.perf_counter() - started
    return {"versions": verified, "markers": markers, "unresolved": unresolved,
            "progress": {"signature": signature, "known": plan_known,
                         "cursor": {"group": next_group, "modem": next_modem},
                         "candidates_total": previous_candidates + candidates},
            "stats": {"status": state, "candidates": candidates,
                      "seconds": round(seconds, 4), "resumed": resumed,
                      "candidates_total": previous_candidates + candidates,
                      "candidates_per_second": round(candidates / seconds) if seconds else 0,
                      "target_count": len(targets), "matched_count": len(targets) - len(unresolved)}}

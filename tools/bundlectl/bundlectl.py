#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

try:
    import nacl.signing
    import nacl.encoding
    HAVE_PYNACL = True
except Exception:
    HAVE_PYNACL = False

try:
    import zstandard as zstd  # type: ignore
    HAVE_ZSTD = True
except Exception:
    HAVE_ZSTD = False


MANIFEST_NAME = "manifest.json"
CONFIG_DIR_DEFAULT = "/etc/videowall"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_files(config_dir: Path) -> List[Path]:
    files: List[Path] = []
    for root, _, filenames in os.walk(config_dir):
        for fn in filenames:
            p = Path(root) / fn
            if p.is_file():
                files.append(p)
    files.sort()
    return files


def manifest_for(config_dir: Path, files: List[Path]) -> Dict[str, Any]:
    rel = lambda p: str(p.relative_to(config_dir))
    return {
        "version": 1,
        "config_dir": str(config_dir),
        "files": [{"path": rel(p), "sha256": sha256_file(p), "size": p.stat().st_size} for p in files],
    }


def manifest_digest(manifest: Dict[str, Any]) -> bytes:
    # Stable encoding
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).digest()


def sign_digest(digest: bytes, privkey_bytes: bytes) -> Dict[str, str]:
    if HAVE_PYNACL:
        sk = nacl.signing.SigningKey(privkey_bytes)
        sig = sk.sign(digest).signature
        return {"alg": "ed25519", "sig": sig.hex()}
    # DEV mode fallback only
    key = privkey_bytes
    sig = hmac.new(key, digest, hashlib.sha256).digest()
    return {"alg": "hmac-sha256-DEV", "sig": sig.hex()}


def verify_sig(digest: bytes, siginfo: Dict[str, str], pubkey_bytes: bytes) -> bool:
    alg = siginfo.get("alg")
    sig = bytes.fromhex(siginfo.get("sig", ""))
    if alg == "ed25519":
        if not HAVE_PYNACL:
            raise RuntimeError("PyNaCl not available but bundle uses ed25519")
        vk = nacl.signing.VerifyKey(pubkey_bytes)
        try:
            vk.verify(digest, sig)
            return True
        except Exception:
            return False
    if alg == "hmac-sha256-DEV":
        # warning: not secure; dev mode only
        expected = hmac.new(pubkey_bytes, digest, hashlib.sha256).digest()
        return hmac.compare_digest(expected, sig)
    raise ValueError(f"unknown signature alg: {alg}")


def load_key(path: Path) -> bytes:
    data = path.read_text(encoding="utf-8").strip()
    # Accept raw hex (32 bytes) or base64 via PyNaCl encoder not assumed.
    if re.fullmatch(r"[0-9a-fA-F]{64}", data):
        return bytes.fromhex(data)
    # If file is binary-like, try raw bytes
    try:
        return path.read_bytes()
    except Exception:
        pass
    raise ValueError("unsupported key format; provide 64 hex chars for ed25519 seed/public key")


def pack_tar_zst(out_path: Path, *, src_dir: Path) -> None:
    # Create tar in-memory then compress with zstd
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w") as tf:
        tf.add(src_dir, arcname=".")
    tar_bytes.seek(0)

    if HAVE_ZSTD:
        cctx = zstd.ZstdCompressor(level=10)
        with open(out_path, "wb") as f:
            f.write(cctx.compress(tar_bytes.read()))
        return

    # Fallback to external zstd binary
    if shutil.which("zstd"):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(tar_bytes.read())
            tmp.flush()
            tmp_path = tmp.name
        try:
            subprocess.check_call(["zstd", "-q", "-10", "-f", tmp_path, "-o", str(out_path)])
        finally:
            try: os.unlink(tmp_path)
            except Exception: pass
        return

    raise RuntimeError("zstandard not available (python module or zstd binary) to create .tar.zst")


def unpack_tar_zst(bundle_path: Path, *, dst_dir: Path) -> None:
    data = bundle_path.read_bytes()
    if HAVE_ZSTD:
        dctx = zstd.ZstdDecompressor()
        tar_data = dctx.decompress(data)
    elif shutil.which("zstd"):
        with tempfile.NamedTemporaryFile(delete=False) as tmp_in:
            tmp_in.write(data)
            tmp_in.flush()
            tmp_in_path = tmp_in.name
        tmp_out = tmp_in_path + ".tar"
        try:
            subprocess.check_call(["zstd", "-q", "-d", "-f", tmp_in_path, "-o", tmp_out])
            tar_data = Path(tmp_out).read_bytes()
        finally:
            for p in [tmp_in_path, tmp_out]:
                try: os.unlink(p)
                except Exception: pass
    else:
        raise RuntimeError("zstandard not available to unpack .tar.zst")

    with tarfile.open(fileobj=io.BytesIO(tar_data), mode="r") as tf:
        tf.extractall(dst_dir)


def cmd_export(args: argparse.Namespace) -> int:
    config_dir = Path(args.config_dir).resolve()
    out_path = Path(args.output).resolve()
    privkey = Path(args.key).read_bytes()

    files = collect_files(config_dir)
    mani = manifest_for(config_dir, files)
    digest = manifest_digest(mani)
    siginfo = sign_digest(digest, privkey)
    mani["signature"] = siginfo

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "config").mkdir()
        # copy config tree
        for p in files:
            dest = td_path / "config" / p.relative_to(config_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(p.read_bytes())
        (td_path / MANIFEST_NAME).write_text(json.dumps(mani, indent=2), encoding="utf-8")
        pack_tar_zst(out_path, src_dir=td_path)

    print(str(out_path))
    if siginfo["alg"] == "hmac-sha256-DEV":
        print("WARNING: PyNaCl unavailable -> using HMAC-SHA256 DEV signature (NOT production)")
    return 0


def verify_bundle(bundle_path: Path, pubkey_path: Path) -> Tuple[bool, str]:
    pubkey = pubkey_path.read_bytes()
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        unpack_tar_zst(bundle_path, dst_dir=td_path)
        mani_path = td_path / MANIFEST_NAME
        if not mani_path.exists():
            return False, "manifest missing"
        mani = json.loads(mani_path.read_text(encoding="utf-8"))
        siginfo = mani.get("signature") or {}
        mani_no_sig = dict(mani)
        mani_no_sig.pop("signature", None)
        digest = manifest_digest(mani_no_sig)
        if not verify_sig(digest, siginfo, pubkey):
            return False, "signature invalid"
        # verify hashes
        cfg_dir = td_path / "config"
        for f in mani.get("files", []):
            rel = f["path"]
            expected = f["sha256"]
            p = cfg_dir / rel
            if not p.exists():
                return False, f"missing file: {rel}"
            got = sha256_file(p)
            if got != expected:
                return False, f"hash mismatch: {rel}"
        return True, "ok"


def cmd_verify(args: argparse.Namespace) -> int:
    ok, msg = verify_bundle(Path(args.bundle), Path(args.pubkey))
    if ok:
        return 0
    print(msg, file=sys.stderr)
    return 1


def stage_dir_for_ring(ring: int) -> Path:
    base = Path("/var/lib/vw-bundles")
    if ring == 0:
        return base / "ring0-staging"
    if ring == 1:
        return base / "ring1-pilot"
    if ring == 2:
        return base / "ring2-full"
    raise ValueError("ring must be 0,1,2")


def cmd_import(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle)
    pubkey = Path(args.pubkey)
    ok, msg = verify_bundle(bundle, pubkey)
    if not ok:
        print(msg, file=sys.stderr)
        return 1

    ring = int(args.ring)
    dst = stage_dir_for_ring(ring)
    dst.mkdir(parents=True, exist_ok=True)
    target = dst / bundle.name
    shutil.copy2(bundle, target)
    print(str(target))
    return 0


def yaml_as_flat_map(doc: Any, prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(doc, dict):
        for k, v in doc.items():
            out.update(yaml_as_flat_map(v, f"{prefix}{k}."))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            out.update(yaml_as_flat_map(v, f"{prefix}{i}."))
    else:
        out[prefix[:-1]] = doc
    return out


def cmd_diff(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle)
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        unpack_tar_zst(bundle, dst_dir=td_path)
        bundle_cfg = td_path / "config"
        config_dir = Path(args.config_dir).resolve()

        diffs = []
        # compare yaml files key-level; for other files do hash compare.
        bfiles = collect_files(bundle_cfg)
        for bf in bfiles:
            rel = bf.relative_to(bundle_cfg)
            cf = config_dir / rel
            if not cf.exists():
                diffs.append((str(rel), "missing_local", None, "present_in_bundle"))
                continue
            if bf.suffix.lower() in (".yaml", ".yml"):
                bdoc = yaml.safe_load(bf.read_text(encoding="utf-8")) or {}
                cdoc = yaml.safe_load(cf.read_text(encoding="utf-8")) or {}
                bmap = yaml_as_flat_map(bdoc)
                cmap = yaml_as_flat_map(cdoc)
                keys = sorted(set(bmap) | set(cmap))
                for k in keys:
                    if bmap.get(k) != cmap.get(k):
                        diffs.append((f"{rel}:{k}", "changed", cmap.get(k), bmap.get(k)))
            else:
                if sha256_file(bf) != sha256_file(cf):
                    diffs.append((str(rel), "changed_binary", "local_hash", "bundle_hash"))

        for d in diffs:
            print(f"{d[0]}\t{d[1]}\tlocal={d[2]}\tbundle={d[3]}")
        return 0 if not diffs else 2


def main() -> int:
    ap = argparse.ArgumentParser(prog="vw-bundlectl", description="Offline bundle CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("export", help="Export config directory into a signed bundle")
    p.add_argument("--output", required=True)
    p.add_argument("--key", required=True, help="ed25519 private key seed bytes (32B) or raw file")
    p.add_argument("--config-dir", default=CONFIG_DIR_DEFAULT)
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("verify", help="Verify a bundle signature and content hashes")
    p.add_argument("--bundle", required=True)
    p.add_argument("--pubkey", required=True)
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("import", help="Verify and stage bundle by rollout ring")
    p.add_argument("--bundle", required=True)
    p.add_argument("--pubkey", required=True)
    p.add_argument("--ring", required=True, choices=["0", "1", "2"])
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("diff", help="Diff bundle config vs local config")
    p.add_argument("--bundle", required=True)
    p.add_argument("--config-dir", default=CONFIG_DIR_DEFAULT)
    p.set_defaults(fn=cmd_diff)

    args = ap.parse_args()
    return int(args.fn(args))

if __name__ == "__main__":
    raise SystemExit(main())

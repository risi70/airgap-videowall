import os
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "bundlectl.py"


def _run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


@pytest.fixture
def tmp_cfg(tmp_path):
    cfg = tmp_path / "etc" / "videowall"
    (cfg / "wallctl").mkdir(parents=True)
    (cfg / "wallctl" / "config.yaml").write_text("wall_id: w1\ncontroller_id: c1\n", encoding="utf-8")
    (cfg / "notes.txt").write_text("hello\n", encoding="utf-8")
    return cfg


def test_export_verify_roundtrip(tmp_path, tmp_cfg):
    # Use PyNaCl keygen
    import nacl.signing
    sk = nacl.signing.SigningKey.generate()
    pk = sk.verify_key

    priv = tmp_path / "sk.bin"
    pub = tmp_path / "pk.bin"
    priv.write_bytes(bytes(sk))
    pub.write_bytes(bytes(pk))

    bundle = tmp_path / "bundle.tar.zst"
    rc, out, err = _run([str(TOOL), "export", "--output", str(bundle), "--key", str(priv), "--config-dir", str(tmp_cfg)])
    assert rc == 0, err
    assert bundle.exists()

    rc, out, err = _run([str(TOOL), "verify", "--bundle", str(bundle), "--pubkey", str(pub)])
    assert rc == 0, err


def test_tampered_bundle_fails_verify(tmp_path, tmp_cfg):
    import nacl.signing
    sk = nacl.signing.SigningKey.generate()
    pk = sk.verify_key

    priv = tmp_path / "sk.bin"
    pub = tmp_path / "pk.bin"
    priv.write_bytes(bytes(sk))
    pub.write_bytes(bytes(pk))

    bundle = tmp_path / "bundle.tar.zst"
    rc, out, err = _run([str(TOOL), "export", "--output", str(bundle), "--key", str(priv), "--config-dir", str(tmp_cfg)])
    assert rc == 0

    # flip one byte
    data = bytearray(bundle.read_bytes())
    data[len(data)//2] ^= 0x01
    bundle.write_bytes(bytes(data))

    rc, out, err = _run([str(TOOL), "verify", "--bundle", str(bundle), "--pubkey", str(pub)])
    assert rc != 0


def test_diff(tmp_path, tmp_cfg):
    import nacl.signing
    sk = nacl.signing.SigningKey.generate()
    pk = sk.verify_key

    priv = tmp_path / "sk.bin"
    pub = tmp_path / "pk.bin"
    priv.write_bytes(bytes(sk))
    pub.write_bytes(bytes(pk))

    bundle = tmp_path / "bundle.tar.zst"
    rc, out, err = _run([str(TOOL), "export", "--output", str(bundle), "--key", str(priv), "--config-dir", str(tmp_cfg)])
    assert rc == 0

    # change local config
    (tmp_cfg / "wallctl" / "config.yaml").write_text("wall_id: w2\ncontroller_id: c1\n", encoding="utf-8")

    rc, out, err = _run([str(TOOL), "diff", "--bundle", str(bundle), "--config-dir", str(tmp_cfg)])
    assert rc in (0, 2)
    assert "wallctl/config.yaml:wall_id" in out

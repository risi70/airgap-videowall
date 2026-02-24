import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "bundlectl.py"

try:
    import nacl.signing  # noqa: F401
    HAVE_PYNACL = True
except ImportError:
    HAVE_PYNACL = False


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


def _make_ed25519_keypair(tmp_path):
    """Generate Ed25519 keypair (requires PyNaCl)."""
    import nacl.signing
    sk = nacl.signing.SigningKey.generate()
    pk = sk.verify_key
    priv = tmp_path / "sk.bin"
    pub = tmp_path / "pk.bin"
    priv.write_bytes(bytes(sk))
    pub.write_bytes(bytes(pk))
    return priv, pub


def _make_hmac_keypair(tmp_path):
    """Generate HMAC key pair (same key for sign and verify -- dev mode only).
    Uses hex-encoded 32-byte key so load_key() returns raw bytes."""
    key = os.urandom(32)
    hex_key = key.hex()
    priv = tmp_path / "hmac-key.bin"
    pub = tmp_path / "hmac-key-pub.bin"
    priv.write_text(hex_key, encoding="utf-8")
    pub.write_text(hex_key, encoding="utf-8")
    return priv, pub


# -- Ed25519 tests (require PyNaCl) ------------------------------------------

@pytest.mark.skipif(not HAVE_PYNACL, reason="PyNaCl not installed")
def test_export_verify_roundtrip_ed25519(tmp_path, tmp_cfg):
    priv, pub = _make_ed25519_keypair(tmp_path)
    bundle = tmp_path / "bundle.tar.zst"

    rc, out, err = _run([str(TOOL), "export", "--output", str(bundle), "--key", str(priv), "--config-dir", str(tmp_cfg)])
    assert rc == 0, err
    assert bundle.exists()

    rc, out, err = _run([str(TOOL), "verify", "--bundle", str(bundle), "--pubkey", str(pub)])
    assert rc == 0, err


@pytest.mark.skipif(not HAVE_PYNACL, reason="PyNaCl not installed")
def test_tampered_bundle_fails_verify_ed25519(tmp_path, tmp_cfg):
    priv, pub = _make_ed25519_keypair(tmp_path)
    bundle = tmp_path / "bundle.tar.zst"

    rc, out, err = _run([str(TOOL), "export", "--output", str(bundle), "--key", str(priv), "--config-dir", str(tmp_cfg)])
    assert rc == 0

    data = bytearray(bundle.read_bytes())
    data[len(data) // 2] ^= 0x01
    bundle.write_bytes(bytes(data))

    rc, out, err = _run([str(TOOL), "verify", "--bundle", str(bundle), "--pubkey", str(pub)])
    assert rc != 0


@pytest.mark.skipif(not HAVE_PYNACL, reason="PyNaCl not installed")
def test_diff_ed25519(tmp_path, tmp_cfg):
    priv, pub = _make_ed25519_keypair(tmp_path)
    bundle = tmp_path / "bundle.tar.zst"

    rc, out, err = _run([str(TOOL), "export", "--output", str(bundle), "--key", str(priv), "--config-dir", str(tmp_cfg)])
    assert rc == 0

    (tmp_cfg / "wallctl" / "config.yaml").write_text("wall_id: w2\ncontroller_id: c1\n", encoding="utf-8")

    rc, out, err = _run([str(TOOL), "diff", "--bundle", str(bundle), "--config-dir", str(tmp_cfg)])
    assert rc in (0, 2)
    assert "wallctl/config.yaml:wall_id" in out


# -- HMAC fallback tests (only when PyNaCl is NOT installed) ------------------
# These verify the dev-mode HMAC-SHA256 fallback path works correctly.
# When PyNaCl is present, bundlectl always uses Ed25519, so HMAC tests
# would fail (mode mismatch). They are only meaningful in PyNaCl-free envs.

@pytest.mark.skipif(HAVE_PYNACL, reason="HMAC fallback only activates without PyNaCl")
def test_export_verify_roundtrip_hmac(tmp_path, tmp_cfg):
    priv, pub = _make_hmac_keypair(tmp_path)
    bundle = tmp_path / "bundle.tar.zst"

    rc, out, err = _run([str(TOOL), "export", "--output", str(bundle), "--key", str(priv), "--config-dir", str(tmp_cfg)])
    assert rc == 0, err
    assert bundle.exists()

    rc, out, err = _run([str(TOOL), "verify", "--bundle", str(bundle), "--pubkey", str(pub)])
    assert rc == 0, err


@pytest.mark.skipif(HAVE_PYNACL, reason="HMAC fallback only activates without PyNaCl")
def test_tampered_bundle_fails_hmac(tmp_path, tmp_cfg):
    priv, pub = _make_hmac_keypair(tmp_path)
    bundle = tmp_path / "bundle.tar.zst"

    rc, out, err = _run([str(TOOL), "export", "--output", str(bundle), "--key", str(priv), "--config-dir", str(tmp_cfg)])
    assert rc == 0

    data = bytearray(bundle.read_bytes())
    data[len(data) // 2] ^= 0x01
    bundle.write_bytes(bytes(data))

    rc, out, err = _run([str(TOOL), "verify", "--bundle", str(bundle), "--pubkey", str(pub)])
    assert rc != 0


@pytest.mark.skipif(HAVE_PYNACL, reason="HMAC fallback only activates without PyNaCl")
def test_diff_hmac(tmp_path, tmp_cfg):
    priv, pub = _make_hmac_keypair(tmp_path)
    bundle = tmp_path / "bundle.tar.zst"

    rc, out, err = _run([str(TOOL), "export", "--output", str(bundle), "--key", str(priv), "--config-dir", str(tmp_cfg)])
    assert rc == 0

    (tmp_cfg / "wallctl" / "config.yaml").write_text("wall_id: w2\ncontroller_id: c1\n", encoding="utf-8")

    rc, out, err = _run([str(TOOL), "diff", "--bundle", str(bundle), "--config-dir", str(tmp_cfg)])
    assert rc in (0, 2)
    assert "wallctl/config.yaml:wall_id" in out

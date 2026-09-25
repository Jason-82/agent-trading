"""FileSigner / NullSigner / generate_keypair: signatures verify, permissions enforced, secrets never shown."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature

from tiller.execution.guard import signature_of
from tiller.execution.wallet import FileSigner, KeyLoadError, NullSigner, PaperCannotSign, generate_keypair

SEED = bytes(range(32))
KP = Keypair.from_seed(SEED)


@pytest.fixture
def keydir(tmp_path: Path) -> Path:
    d = tmp_path / "secrets"
    d.mkdir(mode=0o700)
    os.chmod(d, 0o700)
    return d


def write_key(path: Path, mode: int = 0o600, text: str | None = None) -> Path:
    path.write_text(text if text is not None else json.dumps(list(bytes(KP))))
    os.chmod(path, mode)
    return path


def test_sign_message_verifies_with_solders(keydir: Path) -> None:
    signer = FileSigner.load(write_key(keydir / "id.json"), None)
    assert signer.pubkey == str(KP.pubkey())
    msg = b"familiars challenge nonce 1234"
    sig = signer.sign_message(msg)
    assert len(sig) == 64
    assert Signature.from_bytes(sig).verify(Pubkey.from_string(signer.pubkey), msg)
    assert not Signature.from_bytes(sig).verify(Pubkey.from_string(signer.pubkey), b"other")


def test_world_readable_file_refused(keydir: Path) -> None:
    with pytest.raises(KeyLoadError, match="0600"):
        FileSigner.load(write_key(keydir / "id.json", mode=0o644), None)
    with pytest.raises(KeyLoadError, match="0600"):
        FileSigner.load(write_key(keydir / "id2.json", mode=0o640), None)


def test_group_readable_dir_refused(tmp_path: Path) -> None:
    d = tmp_path / "open"
    d.mkdir()
    os.chmod(d, 0o755)
    with pytest.raises(KeyLoadError, match="0700"):
        FileSigner.load(write_key(d / "id.json"), None)


def test_missing_and_malformed(keydir: Path) -> None:
    with pytest.raises(KeyLoadError):
        FileSigner.load(keydir / "nope.json", None)
    with pytest.raises(KeyLoadError):
        FileSigner.load(write_key(keydir / "bad.json", text="[1,2,3]"), None)
    with pytest.raises(KeyLoadError):
        FileSigner.load(write_key(keydir / "bad2.json", text="not-base58-!!"), None)
    with pytest.raises(KeyLoadError):
        FileSigner.load(None, None)


def test_env_load_json_and_base58() -> None:
    s1 = FileSigner.load(None, json.dumps(list(bytes(KP))))
    s2 = FileSigner.load(None, str(KP))
    assert s1.pubkey == s2.pubkey == str(KP.pubkey())


def test_env_takes_precedence_over_path(keydir: Path) -> None:
    other = Keypair.from_seed(bytes([3] * 32))
    signer = FileSigner.load(write_key(keydir / "id.json"), str(other))
    assert signer.pubkey == str(other.pubkey())


def test_repr_never_contains_secret() -> None:
    signer = FileSigner.from_seed(SEED)
    secret_b58 = str(KP)
    for text in (repr(signer), str(signer)):
        assert signer.pubkey in text and secret_b58 not in text and "secret" not in text.lower()
    import pickle

    with pytest.raises(TypeError):
        pickle.dumps(signer)


def test_null_signer_raises() -> None:
    n = NullSigner(str(KP.pubkey()))
    assert n.pubkey == str(KP.pubkey())
    with pytest.raises(PaperCannotSign):
        n.sign_message(b"x")
    with pytest.raises(PaperCannotSign):
        n.sign_transaction(b"x")


def test_sign_transaction_fills_our_slot(load_fixture) -> None:  # type: ignore[no-untyped-def]
    import base64

    tx = base64.b64decode(load_fixture("jupiter/order_ok")["transaction"])
    signer = FileSigner.from_seed(SEED)
    signed = signer.sign_transaction(tx)
    assert signature_of(signed) == load_fixture("jupiter/execute_ok")["signature"]
    assert len(signed) == len(tx)
    with pytest.raises(KeyLoadError, match="not a required signer"):
        FileSigner.from_seed(bytes([5] * 32)).sign_transaction(tx)


def test_generate_keypair_permissions(tmp_path: Path) -> None:
    path = tmp_path / "keys" / "hot.json"
    pubkey = generate_keypair(path)
    Pubkey.from_string(pubkey)  # valid base58 pubkey
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    loaded = FileSigner.load(path, None)
    assert loaded.pubkey == pubkey
    with pytest.raises(KeyLoadError, match="already exists"):
        generate_keypair(path)

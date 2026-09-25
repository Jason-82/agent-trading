"""Key loading and signing. The ONLY module allowed to import solders' signing (``Keypair``).

* :class:`FileSigner` loads a 64-byte ed25519 keypair from a 0600 file inside a 0700 directory
  (JSON byte array as written by ``solana-keygen``, or a base58 string) or from an environment
  variable; it refuses anything group/world readable, never logs or serialises the secret.
* :class:`NullSigner` carries a pubkey for paper/offline modes and raises on any signing call.
* :func:`generate_keypair` writes a fresh keypair for ``tiller keygen``.

Signatures are 64 raw bytes; ``sign_transaction`` takes and returns serialised
``VersionedTransaction`` bytes with OUR signature slot filled (other slots untouched).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Protocol, runtime_checkable

from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.signature import Signature
from solders.transaction import VersionedTransaction

KEYPAIR_LEN = 64


class PaperCannotSign(RuntimeError):
    """Raised by :class:`NullSigner`: paper and offline modes never sign anything."""


class KeyLoadError(RuntimeError):
    """The keypair file/env var is missing, malformed or has unsafe permissions."""


@runtime_checkable
class Signer(Protocol):
    """Anything that can sign for one wallet."""

    @property
    def pubkey(self) -> str: ...

    def sign_message(self, msg: bytes) -> bytes: ...

    def sign_transaction(self, tx_bytes: bytes) -> bytes: ...


def _parse_secret(text: str) -> Keypair:
    """Accept a JSON array of 64 ints or a base58 string encoding 64 bytes."""
    s = text.strip()
    if s.startswith("["):
        try:
            arr = json.loads(s)
        except json.JSONDecodeError as e:
            raise KeyLoadError("keypair JSON is malformed") from e
        if not isinstance(arr, list) or len(arr) != KEYPAIR_LEN or not all(isinstance(x, int) for x in arr):
            raise KeyLoadError("keypair JSON must be an array of 64 integers")
        try:
            return Keypair.from_bytes(bytes(arr))
        except (ValueError, TypeError) as e:
            raise KeyLoadError("keypair bytes are invalid") from e
    try:
        return Keypair.from_base58_string(s)
    except Exception as e:  # solders raises a generic error type on bad input
        raise KeyLoadError("keypair is neither a JSON byte array nor a valid base58 secret") from e


def _check_permissions(path: Path) -> None:
    st = path.stat()
    if not stat.S_ISREG(st.st_mode):
        raise KeyLoadError(f"{path} is not a regular file")
    if st.st_mode & 0o077:
        raise KeyLoadError(f"{path} must be mode 0600 (group/other bits set)")
    dst = path.parent.stat()
    if dst.st_mode & 0o077:
        raise KeyLoadError(f"{path.parent} must be mode 0700 (group/other bits set)")


class FileSigner:
    """Signer backed by a keypair loaded from disk or the environment."""

    def __init__(self, keypair: Keypair) -> None:
        self._kp = keypair
        self._pubkey = str(keypair.pubkey())

    @classmethod
    def load(cls, path: Path | None, env_secret: str | None) -> FileSigner:
        """Load from ``env_secret`` (the secret VALUE, not the var name) if given, else from ``path``."""
        if env_secret:
            return cls(_parse_secret(env_secret))
        if path is None:
            raise KeyLoadError("no keypair path and no environment secret")
        path = Path(path)
        if not path.exists():
            raise KeyLoadError(f"keypair file {path} does not exist")
        _check_permissions(path)
        return cls(_parse_secret(path.read_text()))

    @classmethod
    def from_seed(cls, seed: bytes) -> FileSigner:
        """Deterministic signer from a 32-byte seed (tests only)."""
        return cls(Keypair.from_seed(seed))

    @property
    def pubkey(self) -> str:
        return self._pubkey

    def sign_message(self, msg: bytes) -> bytes:
        """Detached ed25519 signature (64 bytes) over ``msg``."""
        return bytes(self._kp.sign_message(msg))

    def sign_transaction(self, tx_bytes: bytes) -> bytes:
        """Fill our signature slot of a serialised VersionedTransaction; other slots are kept."""
        tx = VersionedTransaction.from_bytes(tx_bytes)
        msg = tx.message
        n_sig = int(msg.header.num_required_signatures)
        keys = [str(k) for k in msg.account_keys[:n_sig]]
        if self._pubkey not in keys:
            raise KeyLoadError("our wallet is not a required signer of this transaction")
        idx = keys.index(self._pubkey)
        sigs = list(tx.signatures)
        while len(sigs) < n_sig:
            sigs.append(Signature.default())
        sigs[idx] = self._kp.sign_message(to_bytes_versioned(msg))
        return bytes(VersionedTransaction.populate(msg, sigs))

    def __repr__(self) -> str:
        return f"FileSigner(pubkey={self._pubkey})"

    __str__ = __repr__

    def __getstate__(self) -> None:  # never pickled/serialised
        raise TypeError("FileSigner cannot be serialised")


class NullSigner:
    """Pubkey-only signer for paper/offline modes; every signing call raises."""

    def __init__(self, pubkey: str) -> None:
        self._pubkey = pubkey

    @property
    def pubkey(self) -> str:
        return self._pubkey

    def sign_message(self, msg: bytes) -> bytes:
        raise PaperCannotSign("NullSigner refuses to sign messages")

    def sign_transaction(self, tx_bytes: bytes) -> bytes:
        raise PaperCannotSign("NullSigner refuses to sign transactions")

    def __repr__(self) -> str:
        return f"NullSigner(pubkey={self._pubkey})"


def generate_keypair(path: Path) -> str:
    """Write a new keypair as a JSON byte array with mode 0600 (dir 0700). Returns the pubkey."""
    path = Path(path)
    if path.exists():
        raise KeyLoadError(f"{path} already exists; refusing to overwrite a key")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    kp = Keypair()
    payload = json.dumps(list(bytes(kp)))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    os.chmod(path, 0o600)
    return str(kp.pubkey())

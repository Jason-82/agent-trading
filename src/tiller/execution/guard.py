"""Pure pre-sign checks with no I/O.

Every function returns ``None`` when the check passes and a short reason string when it
rejects. Nothing here touches the network, the ledger or a signer; ``solders`` is used only to
deserialise ``VersionedTransaction`` bytes and to derive program addresses.

Units: base units for token amounts (lamports for SOL), fractions for price deviation and
impact (``Decimal('0.01')`` = 1%), basis points for ``fee_bps``/``slippage_bps``.
"""

from __future__ import annotations

import base64
import struct
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from solders.hash import Hash
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from tiller.models import (
    SOL_MINT,
    SYSTEM_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    USDC_MINT,
    Order,
    SwapRequest,
    TokenAccount,
)

ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
JUPITER_V6_PROGRAM = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"

DEFAULT_PROGRAM_ALLOWLIST: frozenset[str] = frozenset(
    {
        SYSTEM_PROGRAM,
        TOKEN_PROGRAM,
        TOKEN_2022_PROGRAM,
        ATA_PROGRAM,
        COMPUTE_BUDGET_PROGRAM,
        MEMO_PROGRAM,
        JUPITER_V6_PROGRAM,
    }
)

TOKEN_PROGRAMS: frozenset[str] = frozenset({TOKEN_PROGRAM, TOKEN_2022_PROGRAM})
STABLE_MINTS: frozenset[str] = frozenset({USDC_MINT})

# SPL Token / Token-2022 instruction discriminators (first data byte).
TOKEN_IX_APPROVE = 4
TOKEN_IX_SET_AUTHORITY = 6
TOKEN_IX_CLOSE_ACCOUNT = 9
TOKEN_IX_APPROVE_CHECKED = 13
DANGEROUS_TOKEN_IX: frozenset[int] = frozenset(
    {TOKEN_IX_APPROVE, TOKEN_IX_SET_AUTHORITY, TOKEN_IX_CLOSE_ACCOUNT, TOKEN_IX_APPROVE_CHECKED}
)

# Fee/rent allowance on the SOL leg: 0.015 SOL in lamports.
LAMPORT_FEE_ALLOWANCE = 15_000_000

TOKEN_ACCOUNT_LEN = 165
MINT_LEN = 82
ACCOUNT_TYPE_OFFSET = 165  # token-2022: one byte after the padded base layout
ACCOUNT_TYPE_MINT = 1
ACCOUNT_TYPE_ACCOUNT = 2

# Token-2022 extension type ids (TLV ``type`` u16) -> jsonParsed name.
EXTENSION_TYPES: dict[int, str] = {
    1: "transferFeeConfig",
    2: "transferFeeAmount",
    3: "mintCloseAuthority",
    4: "confidentialTransferMint",
    5: "confidentialTransferAccount",
    6: "defaultAccountState",
    7: "immutableOwner",
    8: "memoTransfer",
    9: "nonTransferable",
    10: "interestBearingConfig",
    11: "cpiGuard",
    12: "permanentDelegate",
    13: "nonTransferableAccount",
    14: "transferHook",
    15: "transferHookAccount",
    16: "confidentialTransferFeeConfig",
    17: "confidentialTransferFeeAmount",
    18: "metadataPointer",
    19: "tokenMetadata",
    20: "groupPointer",
    21: "tokenGroup",
    22: "groupMemberPointer",
    23: "tokenGroupMember",
    24: "confidentialMintBurn",
    25: "scaledUiAmountConfig",
    26: "pausable",
    27: "pausableAccount",
}

# Extensions that make a Token-2022 mint untradeable for us (spec L1).
DANGEROUS_MINT_EXTENSIONS: frozenset[str] = frozenset(
    {
        "transferFeeConfig",
        "transferHook",
        "permanentDelegate",
        "nonTransferable",
        "pausable",
        "mintCloseAuthority",
        "confidentialTransferMint",
        "confidentialTransferFeeConfig",
        "confidentialMintBurn",
    }
)
# defaultAccountState is dangerous only when the default state is frozen.
EXT_DEFAULT_ACCOUNT_STATE = "defaultAccountState"


class GuardCfg(BaseModel):
    """Thresholds for the pre-sign checks (fractions, bps). See ``[guard]`` in the config."""

    model_config = ConfigDict(extra="forbid")

    max_price_dev_sol: Decimal = Decimal("0.01")
    max_price_dev_token: Decimal = Decimal("0.03")
    max_price_dev_emergency: Decimal = Decimal("0.05")
    max_impact_core: Decimal = Decimal("0.01")
    max_impact_token: Decimal = Decimal("0.03")
    max_fee_bps: int = 10
    routers: set[str] = Field(default_factory=lambda: {"metis", "jupiterz"})
    program_allowlist: set[str] = Field(default_factory=lambda: set(DEFAULT_PROGRAM_ALLOWLIST))
    # Jupiter unwraps native SOL by closing OUR wrapped-SOL ATA back to OUR wallet; that single
    # CloseAccount shape (target = our WSOL ATA, destination = our wallet) is tolerated when true.
    allow_wsol_close_to_self: bool = True


class SimExpectations(BaseModel):
    """What the simulation must show for the order to be signed. Amounts in base units."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    wallet: str
    input_mint: str
    output_mint: str
    in_base: int
    min_out_base: int
    max_lamport_drop: int
    owned_token_accounts: list[TokenAccount]
    pre_lamports: int
    input_account: str | None = None
    output_account: str | None = None


# --------------------------------------------------------------------------- helpers


def derive_ata(owner: str, mint: str, token_program: str = TOKEN_PROGRAM) -> str:
    """Associated token account address of ``owner`` for ``mint`` under ``token_program``."""
    seeds = [
        bytes(Pubkey.from_string(owner)),
        bytes(Pubkey.from_string(token_program)),
        bytes(Pubkey.from_string(mint)),
    ]
    addr, _bump = Pubkey.find_program_address(seeds, Pubkey.from_string(ATA_PROGRAM))
    return str(addr)


def is_core_pair(input_mint: str, output_mint: str) -> bool:
    """SOL/USDC in either direction."""
    return {input_mint, output_mint} == {SOL_MINT, USDC_MINT}


def signature_of(signed_tx_bytes: bytes) -> str:
    """Base58 transaction signature (the fee payer's) of already-signed transaction bytes."""
    tx = VersionedTransaction.from_bytes(signed_tx_bytes)
    if not tx.signatures or tx.signatures[0] == Signature.default():
        raise ValueError("transaction is not signed by the fee payer")
    return str(tx.signatures[0])


def implied_price(order: Order, req: SwapRequest, in_decimals: int, out_decimals: int) -> Decimal:
    """USD price of the non-stable leg implied by the quote (exactly one leg must be USDC)."""
    in_units = Decimal(order.in_amount) / Decimal(10**in_decimals)
    out_units = Decimal(order.out_amount) / Decimal(10**out_decimals)
    if in_units <= 0 or out_units <= 0:
        raise ValueError("quote has a zero leg")
    if req.output_mint in STABLE_MINTS and req.input_mint not in STABLE_MINTS:
        return out_units / in_units  # selling token for USDC: USD received per token
    if req.input_mint in STABLE_MINTS and req.output_mint not in STABLE_MINTS:
        return in_units / out_units  # buying token with USDC: USD paid per token
    raise ValueError("unsupported pair: exactly one leg must be USDC")


def _pubkey_b58(raw: bytes) -> str:
    return str(Pubkey.from_bytes(raw))


def _account_data_bytes(account: dict[str, Any]) -> bytes | None:
    """Raw bytes of an account value when it came back as ``[b64, 'base64']``."""
    data = account.get("data")
    if isinstance(data, list) and len(data) == 2 and data[1] == "base64" and isinstance(data[0], str):
        try:
            return base64.b64decode(data[0], validate=True)
        except (ValueError, TypeError):
            return None
    return None


# --------------------------------------------------------------------------- raw layouts


def parse_token_account_bytes(data: bytes) -> dict[str, Any]:
    """Decode the 165-byte SPL token account layout (plus Token-2022 extensions if present).

    Returns ``{"mint", "owner", "amount", "delegate", "state", "is_native", "close_authority",
    "extensions"}`` with base58 pubkeys, ``amount`` in base units and ``None`` for unset options.
    """
    if len(data) < TOKEN_ACCOUNT_LEN:
        raise ValueError(f"token account data too short: {len(data)} bytes")
    if len(data) > TOKEN_ACCOUNT_LEN and data[ACCOUNT_TYPE_OFFSET] != ACCOUNT_TYPE_ACCOUNT:
        raise ValueError("data longer than 165 bytes but account type byte is not Account")
    mint = _pubkey_b58(data[0:32])
    owner = _pubkey_b58(data[32:64])
    (amount,) = struct.unpack_from("<Q", data, 64)
    (delegate_opt,) = struct.unpack_from("<I", data, 72)
    delegate = _pubkey_b58(data[76:108]) if delegate_opt == 1 else None
    state = data[108]
    (native_opt,) = struct.unpack_from("<I", data, 109)
    (delegated_amount,) = struct.unpack_from("<Q", data, 121)
    (close_opt,) = struct.unpack_from("<I", data, 129)
    close_authority = _pubkey_b58(data[133:165]) if close_opt == 1 else None
    extensions = _parse_tlv(data[ACCOUNT_TYPE_OFFSET + 1 :]) if len(data) > ACCOUNT_TYPE_OFFSET + 1 else []
    return {
        "mint": mint,
        "owner": owner,
        "amount": int(amount),
        "delegate": delegate,
        "delegated_amount": int(delegated_amount),
        "state": {0: "uninitialized", 1: "initialized", 2: "frozen"}.get(state, f"unknown:{state}"),
        "is_native": native_opt == 1,
        "close_authority": close_authority,
        "extensions": extensions,
    }


def _parse_tlv(buf: bytes) -> list[dict[str, Any]]:
    """Token-2022 TLV extension list: ``[{"type": id, "name": str, "data": bytes}]``."""
    out: list[dict[str, Any]] = []
    pos = 0
    while pos + 4 <= len(buf):
        ext_type, ext_len = struct.unpack_from("<HH", buf, pos)
        pos += 4
        if ext_type == 0:  # Uninitialized / padding
            break
        body = buf[pos : pos + ext_len]
        if len(body) < ext_len:
            raise ValueError("truncated TLV extension")
        pos += ext_len
        out.append(
            {"type": ext_type, "name": EXTENSION_TYPES.get(ext_type, f"unknown:{ext_type}"), "data": body}
        )
    return out


def parse_mint_bytes(data: bytes) -> dict[str, Any]:
    """Decode the 82-byte mint layout plus Token-2022 TLV extensions into jsonParsed-like fields."""
    if len(data) < MINT_LEN:
        raise ValueError(f"mint data too short: {len(data)} bytes")
    (mint_opt,) = struct.unpack_from("<I", data, 0)
    mint_authority = _pubkey_b58(data[4:36]) if mint_opt == 1 else None
    (supply,) = struct.unpack_from("<Q", data, 36)
    decimals = data[44]
    initialized = data[45] == 1
    (freeze_opt,) = struct.unpack_from("<I", data, 46)
    freeze_authority = _pubkey_b58(data[50:82]) if freeze_opt == 1 else None
    extensions: list[dict[str, Any]] = []
    if len(data) > ACCOUNT_TYPE_OFFSET:
        if data[ACCOUNT_TYPE_OFFSET] != ACCOUNT_TYPE_MINT:
            raise ValueError("extended mint data without Mint account type byte")
        for ext in _parse_tlv(data[ACCOUNT_TYPE_OFFSET + 1 :]):
            entry: dict[str, Any] = {"extension": ext["name"]}
            if ext["name"] == EXT_DEFAULT_ACCOUNT_STATE and ext["data"]:
                entry["state"] = {
                    "accountState": {1: "initialized", 2: "frozen"}.get(ext["data"][0], "unknown")
                }
            extensions.append(entry)
    return {
        "mintAuthority": mint_authority,
        "freezeAuthority": freeze_authority,
        "supply": str(supply),
        "decimals": int(decimals),
        "isInitialized": initialized,
        "extensions": extensions,
    }


# --------------------------------------------------------------------------- mint checks


def _mint_program(mint_account: dict[str, Any]) -> str | None:
    owner = mint_account.get("owner")
    return str(owner) if isinstance(owner, str) else None


def mint_info(mint_account: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """``(program_id, info)`` from a jsonParsed or base64 ``getAccountInfo`` value; info None if unparseable."""
    program = _mint_program(mint_account)
    data = mint_account.get("data")
    if isinstance(data, dict):
        parsed = data.get("parsed")
        if isinstance(parsed, dict) and parsed.get("type") == "mint" and isinstance(parsed.get("info"), dict):
            return program, dict(parsed["info"])
        return program, None
    raw = _account_data_bytes(mint_account)
    if raw is None:
        return program, None
    try:
        return program, parse_mint_bytes(raw)
    except ValueError:
        return program, None


def mint_decimals(mint_account: dict[str, Any]) -> int:
    """Decimals of a mint account value; raises ``ValueError`` if the account is not a mint."""
    _program, info = mint_info(mint_account)
    if info is None or "decimals" not in info:
        raise ValueError("not a parseable mint account")
    return int(info["decimals"])


def check_mint(mint_account: dict[str, Any] | None) -> str | None:
    """Binding mint rules shared with the token gate.

    Rejects: missing account; owner not a token program; token-2022 with a dangerous extension
    (transferFeeConfig, transferHook, permanentDelegate, nonTransferable, defaultAccountState
    frozen, pausable, confidentialTransfer*, mintCloseAuthority); mint or freeze authority set.
    """
    if mint_account is None:
        return "mint: account not found"
    program, info = mint_info(mint_account)
    if program not in TOKEN_PROGRAMS:
        return f"mint: owner program {program} is not a token program"
    if info is None:
        return "mint: account data is not a parseable mint"
    if info.get("isInitialized") is False:
        return "mint: not initialized"
    if info.get("mintAuthority") not in (None, ""):
        return "mint: mint authority present"
    if info.get("freezeAuthority") not in (None, ""):
        return "mint: freeze authority present"
    if program == TOKEN_2022_PROGRAM:
        for ext in info.get("extensions") or []:
            name = ext.get("extension") if isinstance(ext, dict) else None
            if name is None:
                return "mint: unparseable token-2022 extension"
            if name in DANGEROUS_MINT_EXTENSIONS:
                return f"mint: token-2022 extension {name}"
            if name == EXT_DEFAULT_ACCOUNT_STATE:
                state = (ext.get("state") or {}).get("accountState")
                if state != "initialized":
                    return f"mint: token-2022 defaultAccountState {state}"
            if str(name).startswith("unknown"):
                return f"mint: unknown token-2022 extension {name}"
    return None


# --------------------------------------------------------------------------- quote checks


def price_deviation_limit(req: SwapRequest, cfg: GuardCfg) -> Decimal:
    if req.mode == "emergency":
        return cfg.max_price_dev_emergency
    if is_core_pair(req.input_mint, req.output_mint):
        return cfg.max_price_dev_sol
    return cfg.max_price_dev_token


def impact_limit(req: SwapRequest, cfg: GuardCfg) -> Decimal:
    if is_core_pair(req.input_mint, req.output_mint):
        return cfg.max_impact_core
    return cfg.max_impact_token


def check_quote(
    req: SwapRequest,
    order: Order,
    ref_price_usd: Decimal,
    in_decimals: int,
    out_decimals: int,
    cfg: GuardCfg,
) -> str | None:
    """Reject a quote whose implied price, impact, fee tier or router is off.

    ``ref_price_usd`` is the independent USD reference of the non-USDC leg.
    """
    if order.in_amount != req.amount_base:
        return f"quote: inAmount {order.in_amount} != requested {req.amount_base}"
    if order.out_amount <= 0:
        return "quote: zero outAmount"
    if ref_price_usd <= 0:
        return "quote: no reference price"
    try:
        px = implied_price(order, req, in_decimals, out_decimals)
    except ValueError as e:
        return f"quote: {e}"
    dev = abs(px / ref_price_usd - Decimal(1))
    limit = price_deviation_limit(req, cfg)
    if dev > limit:
        return f"quote: implied price {px:.6f} deviates {dev:.4%} from reference {ref_price_usd:.6f} (limit {limit:.2%})"
    impact = abs(order.price_impact_pct)
    if impact > impact_limit(req, cfg):
        return f"quote: price impact {impact:.4%} above limit"
    if order.fee_bps > cfg.max_fee_bps:
        return f"quote: feeBps {order.fee_bps} above max {cfg.max_fee_bps}"
    if order.router not in cfg.routers:
        return f"quote: router {order.router!r} not allowlisted"
    return None


# --------------------------------------------------------------------------- transaction checks


def check_transaction(
    tx_bytes: bytes, our_pubkey: str, owned_token_accounts: list[str], cfg: GuardCfg
) -> str | None:
    """Static checks on the unsigned transaction Jupiter returned (see spec execution rule 5)."""
    try:
        tx = VersionedTransaction.from_bytes(tx_bytes)
    except Exception as e:  # solders raises a variety of ValueError-ish types
        return f"tx: undecodable ({type(e).__name__})"
    msg = tx.message
    keys = [str(k) for k in msg.account_keys]
    header = msg.header
    n_sig = int(header.num_required_signatures)
    if not keys:
        return "tx: no account keys"
    if n_sig < 1:
        return "tx: no required signers"
    if keys[0] != our_pubkey:
        return f"tx: fee payer {keys[0]} is not our wallet"
    if len(tx.signatures) != n_sig:
        return "tx: signature count does not match required signers"
    for i in range(1, n_sig):
        if tx.signatures[i] == Signature.default():
            return f"tx: required signer {keys[i]} has no signature"
        if keys[i] == our_pubkey:
            return "tx: our wallet listed twice as signer"
    if msg.recent_blockhash == Hash.default():
        return "tx: missing recent blockhash"

    owned = set(owned_token_accounts)
    wsol_ata = derive_ata(our_pubkey, SOL_MINT, TOKEN_PROGRAM)
    for ix in msg.instructions:
        pid_idx = int(ix.program_id_index)
        if pid_idx >= len(keys):
            return "tx: program id resolved through a lookup table"
        program = keys[pid_idx]
        if program not in cfg.program_allowlist:
            return f"tx: program {program} not allowlisted"
        if program in TOKEN_PROGRAMS:
            data = bytes(ix.data)
            if not data:
                return "tx: empty token instruction"
            op = data[0]
            if op not in DANGEROUS_TOKEN_IX:
                continue
            accounts = list(ix.accounts)
            if not accounts:
                return "tx: token instruction without accounts"
            if accounts[0] >= len(keys):
                return f"tx: token op {op} target resolved through a lookup table"
            target = keys[accounts[0]]
            if target == our_pubkey:
                return f"tx: token op {op} targets our wallet"
            if target in owned or target == wsol_ata:
                if (
                    op == TOKEN_IX_CLOSE_ACCOUNT
                    and cfg.allow_wsol_close_to_self
                    and target == wsol_ata
                    and len(accounts) >= 3
                    and accounts[1] < len(keys)
                    and accounts[2] < len(keys)
                    and keys[accounts[1]] == our_pubkey
                    and keys[accounts[2]] == our_pubkey
                ):
                    continue
                return f"tx: token op {op} targets our token account {target}"
    return None


# --------------------------------------------------------------------------- simulation checks


def _parse_sim_token_account(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Post-state token account from a simulation entry (jsonParsed or base64), None if not one."""
    data = entry.get("data")
    if isinstance(data, dict):
        parsed = data.get("parsed")
        if not isinstance(parsed, dict) or parsed.get("type") != "account":
            return None
        info = parsed.get("info") or {}
        amount = (info.get("tokenAmount") or {}).get("amount")
        if amount is None or "mint" not in info or "owner" not in info:
            return None
        return {
            "mint": str(info["mint"]),
            "owner": str(info["owner"]),
            "amount": int(amount),
            "delegate": info.get("delegate"),
            "close_authority": info.get("closeAuthority"),
            "state": info.get("state"),
        }
    raw = _account_data_bytes(entry)
    if raw is None:
        return None
    if str(entry.get("owner")) not in TOKEN_PROGRAMS:
        return None
    try:
        return parse_token_account_bytes(raw)
    except ValueError:
        return None


def check_simulation(sim: Any, expect: SimExpectations) -> str | None:
    """Reject on any post-simulation state that is not exactly the swap we asked for.

    ``sim`` is an ``execution.rpc.SimResult`` (duck-typed: ``err``, ``accounts``,
    ``accounts_requested``). Lamport bounds use ``expect.pre_lamports`` / ``max_lamport_drop``.
    """
    if sim.err is not None:
        return f"sim: error {sim.err!r}"
    requested = list(sim.accounts_requested)
    accounts = list(sim.accounts)
    if not requested or len(requested) != len(accounts):
        return "sim: account snapshot count does not match request"
    post: dict[str, dict[str, Any] | None] = dict(zip(requested, accounts, strict=True))
    if expect.wallet not in post:
        return "sim: wallet not in snapshot"

    wallet = post[expect.wallet]
    if wallet is None:
        return "sim: wallet account missing after simulation"
    if str(wallet.get("owner")) != SYSTEM_PROGRAM:
        return f"sim: wallet owner changed to {wallet.get('owner')}"
    lamports = int(wallet.get("lamports", 0))
    drop = expect.pre_lamports - lamports
    if drop > expect.max_lamport_drop:
        return f"sim: lamport drop {drop} exceeds allowance {expect.max_lamport_drop}"
    if expect.output_mint == SOL_MINT:
        rise = lamports - expect.pre_lamports
        if rise < expect.min_out_base - LAMPORT_FEE_ALLOWANCE:
            return f"sim: SOL output rise {rise} below minimum {expect.min_out_base - LAMPORT_FEE_ALLOWANCE}"

    pre = {a.pubkey: a for a in expect.owned_token_accounts}
    output_seen = False
    for addr in requested:
        if addr == expect.wallet:
            continue
        entry = post[addr]
        before = pre.get(addr)
        if entry is None:
            if before is None:
                if addr == expect.output_account and expect.output_mint != SOL_MINT:
                    return "sim: output token account absent after simulation"
                continue
            if before.mint == SOL_MINT:
                continue  # wrapped-SOL ATA unwrapped into lamports (bounded above)
            return f"sim: token account {addr} ({before.mint}) closed"
        acc = _parse_sim_token_account(entry)
        if acc is None:
            if before is None and addr in (expect.input_account, expect.output_account):
                return f"sim: account {addr} is not a token account after simulation"
            return f"sim: unparseable token account {addr}"
        if acc["owner"] != expect.wallet:
            return f"sim: token account {addr} owner is {acc['owner']}"
        if acc.get("delegate") not in (None, ""):
            return f"sim: token account {addr} has delegate {acc['delegate']}"
        if acc.get("close_authority") not in (None, "", expect.wallet):
            return f"sim: token account {addr} close authority {acc['close_authority']}"
        if acc.get("state") == "frozen":
            return f"sim: token account {addr} frozen"
        mint = str(acc["mint"])
        if before is not None and before.mint != mint:
            return f"sim: token account {addr} mint changed"
        delta = int(acc["amount"]) - (before.amount_base if before is not None else 0)
        if mint == SOL_MINT:
            continue  # native leg is measured in lamports
        if mint == expect.input_mint:
            if -delta > expect.in_base:
                return f"sim: input drop {-delta} exceeds inAmount {expect.in_base}"
        elif mint == expect.output_mint:
            output_seen = True
            if delta < expect.min_out_base:
                return f"sim: output rise {delta} below minimum {expect.min_out_base}"
        elif delta < 0:
            return f"sim: unrelated token account {addr} ({mint}) decreased by {-delta}"
    if expect.output_mint != SOL_MINT and not output_seen:
        return "sim: output token account not in snapshot"
    return None

"""Guard: quote tiers, real solders transactions (adversarial variants), simulation fixtures, mint table."""

from __future__ import annotations

import base64
import struct
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message, MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from tiller.execution.guard import (
    ATA_PROGRAM,
    COMPUTE_BUDGET_PROGRAM,
    JUPITER_V6_PROGRAM,
    LAMPORT_FEE_ALLOWANCE,
    GuardCfg,
    SimExpectations,
    check_mint,
    check_quote,
    check_simulation,
    check_transaction,
    derive_ata,
    mint_decimals,
    parse_mint_bytes,
    parse_token_account_bytes,
    signature_of,
)
from tiller.execution.rpc import SimResult, parse_token_account_value
from tiller.models import (
    SOL_MINT,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    USDC_MINT,
    Order,
    SwapRequest,
    TokenAccount,
)

WALLET_KP = Keypair.from_seed(bytes(range(32)))
FOREIGN_KP = Keypair.from_seed(bytes([7] * 32))
WALLET = str(WALLET_KP.pubkey())
FOREIGN = str(FOREIGN_KP.pubkey())
TOKEN_X = "J2xccRtuG43drESLYznHhLhQkLTdfepcKYbiQ9BsJVaf"
USDC_ATA = derive_ata(WALLET, USDC_MINT)
WSOL_ATA = derive_ata(WALLET, SOL_MINT)
TOKENX_ATA = derive_ata(WALLET, TOKEN_X)
BLOCKHASH = Hash.from_bytes(bytes([0xAB] * 32))
PK = Pubkey.from_string
CFG = GuardCfg()


def make_order(fixture: dict[str, Any], **over: Any) -> Order:
    data = {**fixture, **over}
    data["raw"] = fixture
    return Order.model_validate(data)


def req(
    input_mint: str = USDC_MINT, output_mint: str = SOL_MINT, amount: int = 100_000_000, mode: str = "normal"
) -> SwapRequest:
    return SwapRequest(
        input_mint=input_mint,
        output_mint=output_mint,
        amount_base=amount,
        strategy="t",
        reason="r",
        mode=mode,
    )  # type: ignore[arg-type]


# --------------------------------------------------------------------------- quote


def test_quote_happy_path(load_fixture: Callable[[str], Any]) -> None:
    order = make_order(load_fixture("jupiter/order_ok"))
    assert check_quote(req(), order, Decimal("150"), 6, 9, CFG) is None


def test_quote_core_tier_1pct(load_fixture: Callable[[str], Any]) -> None:
    order = make_order(load_fixture("jupiter/order_ok"))  # implied ~150.195
    assert check_quote(req(), order, Decimal("151.0"), 6, 9, CFG) is None  # 0.53%
    reason = check_quote(req(), order, Decimal("152.0"), 6, 9, CFG)  # 1.19%
    assert reason is not None and reason.startswith("quote: implied price")


def test_quote_token_tier_3pct(load_fixture: Callable[[str], Any]) -> None:
    fx = load_fixture("jupiter/order_ok")
    order = make_order(fx, outAmount="2000000000")  # 100 USDC -> 2000 TKX => 0.05 USD
    r = req(USDC_MINT, TOKEN_X)
    assert check_quote(r, order, Decimal("0.051"), 6, 6, CFG) is None  # ~2%
    assert check_quote(r, order, Decimal("0.052"), 6, 6, CFG) is not None  # ~3.8%
    assert check_quote(r, order, Decimal("0.052"), 6, 6, CFG).startswith("quote: implied price")  # type: ignore[union-attr]


def test_quote_emergency_tier_5pct(load_fixture: Callable[[str], Any]) -> None:
    order = make_order(load_fixture("jupiter/order_ok"))
    r = req(mode="emergency")
    assert check_quote(r, order, Decimal("156.0"), 6, 9, CFG) is None  # 3.7%
    assert check_quote(r, order, Decimal("159.0"), 6, 9, CFG) is not None  # 5.5%


def test_quote_impact_fee_router_price(load_fixture: Callable[[str], Any]) -> None:
    assert "impact" in check_quote(
        req(), make_order(load_fixture("jupiter/order_high_impact")), Decimal("150"), 6, 9, CFG
    )  # type: ignore[operator]
    assert "feeBps 50" in check_quote(
        req(), make_order(load_fixture("jupiter/order_fee50")), Decimal("150"), 6, 9, CFG
    )  # type: ignore[operator]
    assert "router" in check_quote(
        req(), make_order(load_fixture("jupiter/order_bad_router")), Decimal("150"), 6, 9, CFG
    )  # type: ignore[operator]
    assert "implied price" in check_quote(
        req(), make_order(load_fixture("jupiter/order_bad_price")), Decimal("150"), 6, 9, CFG
    )  # type: ignore[operator]
    # token impact tier is 3%: 2.5% passes for a token pair but not for the core pair
    tok = make_order(load_fixture("jupiter/order_high_impact"), outAmount="2000000000")
    assert check_quote(req(USDC_MINT, TOKEN_X), tok, Decimal("0.05"), 6, 6, CFG) is None


def test_quote_rejects_amount_mismatch_and_bad_pair(load_fixture: Callable[[str], Any]) -> None:
    order = make_order(load_fixture("jupiter/order_ok"))
    assert "inAmount" in check_quote(req(amount=1), order, Decimal("150"), 6, 9, CFG)  # type: ignore[operator]
    assert "one leg must be USDC" in check_quote(req(SOL_MINT, TOKEN_X), order, Decimal("150"), 9, 6, CFG)  # type: ignore[operator]
    assert check_quote(req(), order, Decimal("0"), 6, 9, CFG) == "quote: no reference price"


# --------------------------------------------------------------------------- transaction builders


def ix_transfer(frm: str, to: str, lamports: int = 1000) -> Instruction:
    return transfer(TransferParams(from_pubkey=PK(frm), to_pubkey=PK(to), lamports=lamports))


def ix_token(
    op: int, accounts: list[tuple[str, bool]], program: str = TOKEN_PROGRAM, data_tail: bytes = b""
) -> Instruction:
    metas = [AccountMeta(PK(a), signer, True) for a, signer in accounts]
    return Instruction(PK(program), bytes([op]) + data_tail, metas)


def build(
    instructions: list[Instruction],
    payer: str = WALLET,
    signers: list[Keypair] | None = None,
    blockhash: Hash = BLOCKHASH,
    v0: bool = False,
) -> bytes:
    msg: Any
    if v0:
        msg = MessageV0.try_compile(PK(payer), instructions, [], blockhash)
    else:
        msg = Message.new_with_blockhash(instructions, PK(payer), blockhash)
    n = msg.header.num_required_signatures
    keys = [str(k) for k in msg.account_keys[:n]]
    sigs = [Signature.default()] * n
    for kp in signers or []:
        sigs[keys.index(str(kp.pubkey()))] = kp.sign_message(to_bytes_versioned(msg))
    return bytes(VersionedTransaction.populate(msg, sigs))


def test_tx_happy_path_legacy_and_v0() -> None:
    for v0 in (False, True):
        tx = build([ix_transfer(WALLET, FOREIGN)], v0=v0)
        assert check_transaction(tx, WALLET, [USDC_ATA], CFG) is None


def test_tx_fee_payer_mismatch() -> None:
    tx = build([ix_transfer(FOREIGN, WALLET)], payer=FOREIGN)
    assert "fee payer" in check_transaction(tx, WALLET, [], CFG)  # type: ignore[operator]


def test_tx_unsigned_foreign_signer_rejected_signed_accepted() -> None:
    unsigned = build([ix_transfer(FOREIGN, WALLET)], payer=WALLET)
    reason = check_transaction(unsigned, WALLET, [], CFG)
    assert reason is not None and "no signature" in reason and FOREIGN in reason
    presigned = build([ix_transfer(FOREIGN, WALLET)], payer=WALLET, signers=[FOREIGN_KP])
    assert check_transaction(presigned, WALLET, [], CFG) is None


def test_tx_program_not_allowlisted() -> None:
    evil = str(Keypair.from_seed(bytes([99] * 32)).pubkey())
    ix = Instruction(PK(evil), b"\x01", [AccountMeta(PK(WALLET), True, True)])
    tx = build([ix])
    assert "not allowlisted" in check_transaction(tx, WALLET, [], CFG)  # type: ignore[operator]
    wide = GuardCfg(program_allowlist={*CFG.program_allowlist, evil})
    assert check_transaction(tx, WALLET, [], wide) is None


@pytest.mark.parametrize("op", [4, 6, 9, 13])
def test_tx_dangerous_token_ops_on_our_ata_rejected(op: int) -> None:
    ix = ix_token(op, [(USDC_ATA, False), (FOREIGN, False), (WALLET, True)], data_tail=struct.pack("<Q", 1))
    tx = build([ix])
    reason = check_transaction(tx, WALLET, [USDC_ATA, TOKENX_ATA], CFG)
    assert reason is not None and f"token op {op}" in reason and USDC_ATA in reason
    # same op on a foreign token account is not our problem
    tx2 = build([ix_token(op, [(FOREIGN, False), (FOREIGN, False), (WALLET, True)])])
    assert check_transaction(tx2, WALLET, [USDC_ATA], CFG) is None


def test_tx_token2022_approve_rejected() -> None:
    ix = ix_token(4, [(TOKENX_ATA, False), (FOREIGN, False), (WALLET, True)], program=TOKEN_2022_PROGRAM)
    assert "token op 4" in check_transaction(build([ix]), WALLET, [TOKENX_ATA], CFG)  # type: ignore[operator]


def test_tx_benign_token_transfer_passes() -> None:
    ix = ix_token(3, [(USDC_ATA, False), (FOREIGN, False), (WALLET, True)], data_tail=struct.pack("<Q", 5))
    assert check_transaction(build([ix]), WALLET, [USDC_ATA], CFG) is None


def test_tx_wsol_close_to_self_allowed_only_when_configured() -> None:
    close_ok = ix_token(9, [(WSOL_ATA, False), (WALLET, False), (WALLET, True)])
    assert check_transaction(build([close_ok]), WALLET, [WSOL_ATA], CFG) is None
    strict = GuardCfg(allow_wsol_close_to_self=False)
    assert "token op 9" in check_transaction(build([close_ok]), WALLET, [WSOL_ATA], strict)  # type: ignore[operator]
    close_elsewhere = ix_token(9, [(WSOL_ATA, False), (FOREIGN, False), (WALLET, True)])
    assert "token op 9" in check_transaction(build([close_elsewhere]), WALLET, [], CFG)  # type: ignore[operator]


def test_tx_missing_blockhash_and_garbage() -> None:
    tx = build([ix_transfer(WALLET, FOREIGN)], blockhash=Hash.default())
    assert check_transaction(tx, WALLET, [], CFG) == "tx: missing recent blockhash"
    assert check_transaction(b"\x00\x01garbage", WALLET, [], CFG).startswith("tx: undecodable")  # type: ignore[union-attr]


def test_tx_order_fixture_passes(load_fixture: Callable[[str], Any]) -> None:
    tx = base64.b64decode(load_fixture("jupiter/order_ok")["transaction"])
    assert check_transaction(tx, WALLET, [USDC_ATA, TOKENX_ATA], CFG) is None
    assert check_transaction(tx, FOREIGN, [], CFG).startswith("tx: fee payer")  # type: ignore[union-attr]


def test_signature_of_matches_execute_fixture(load_fixture: Callable[[str], Any]) -> None:
    from tiller.execution.wallet import FileSigner

    tx = base64.b64decode(load_fixture("jupiter/order_ok")["transaction"])
    with pytest.raises(ValueError):
        signature_of(tx)
    signed = FileSigner.from_seed(bytes(range(32))).sign_transaction(tx)
    assert signature_of(signed) == load_fixture("jupiter/execute_ok")["signature"]


def test_allowlist_constants_cover_spec() -> None:
    for p in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM, ATA_PROGRAM, COMPUTE_BUDGET_PROGRAM, JUPITER_V6_PROGRAM):
        assert p in CFG.program_allowlist


# --------------------------------------------------------------------------- simulation


def sim_from_fixture(fx: dict[str, Any]) -> SimResult:
    v = fx["result"]["value"]
    return SimResult(
        err=v["err"],
        logs=v["logs"],
        units_consumed=v["unitsConsumed"],
        accounts=v["accounts"],
        accounts_requested=fx["_meta"]["addresses"],
    )


def owned(load_fixture: Callable[[str], Any]) -> list[TokenAccount]:
    rows = load_fixture("rpc/token_accounts_spl")["result"]["value"]
    return [parse_token_account_value(r["pubkey"], r["account"]) for r in rows]  # type: ignore[misc]


def expectations(load_fixture: Callable[[str], Any], **over: Any) -> SimExpectations:
    base = dict(
        wallet=WALLET,
        input_mint=USDC_MINT,
        output_mint=SOL_MINT,
        in_base=100_000_000,
        min_out_base=665_800_000 * 9950 // 10_000,
        max_lamport_drop=LAMPORT_FEE_ALLOWANCE,
        owned_token_accounts=owned(load_fixture),
        pre_lamports=2_000_000_000,
        input_account=USDC_ATA,
        output_account=WSOL_ATA,
    )
    base.update(over)
    return SimExpectations(**base)


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("rpc/sim_ok", None),
        ("rpc/sim_ok_base64", None),
        ("rpc/sim_err", "sim: error"),
        ("rpc/sim_delegate_set", "has delegate"),
        ("rpc/sim_delegate_set_base64", "has delegate"),
        ("rpc/sim_owner_reassigned", "owner is"),
        ("rpc/sim_close_authority", "close authority"),
        ("rpc/sim_wallet_owner_changed", "wallet owner changed"),
        ("rpc/sim_sol_overdraw", "lamport drop"),
        ("rpc/sim_output_short", "SOL output rise"),
        ("rpc/sim_input_overdrawn", "input drop"),
        ("rpc/sim_unrelated_decrease", "unrelated token account"),
        ("rpc/sim_frozen", "frozen"),
        ("rpc/sim_short_snapshot", "snapshot count"),
    ],
)
def test_simulation_fixtures(load_fixture: Callable[[str], Any], fixture: str, expected: str | None) -> None:
    reason = check_simulation(sim_from_fixture(load_fixture(fixture)), expectations(load_fixture))
    if expected is None:
        assert reason is None
    else:
        assert reason is not None and expected in reason


def test_simulation_token_output_branches(load_fixture: Callable[[str], Any]) -> None:
    fx = load_fixture("rpc/sim_ok")
    sim = sim_from_fixture(fx)
    # Buying TOKEN_X with USDC: fixture shows TOKEN_X unchanged => output rise short
    exp = expectations(load_fixture, output_mint=TOKEN_X, min_out_base=1_000, output_account=TOKENX_ATA)
    assert "output rise" in check_simulation(sim, exp)  # type: ignore[operator]
    # output account absent after simulation for a token output
    other = "5Z6Ay5NEcbg3xhopc522sBCRXQujkTiuDRnHGfQdcnSf"
    exp2 = expectations(load_fixture, output_mint=other, min_out_base=1, output_account=WSOL_ATA)
    assert "output token account absent" in check_simulation(sim, exp2)  # type: ignore[operator]
    # selling SOL: lamport drop allowance includes the input
    sell = load_fixture("rpc/sim_sol_overdraw")
    exp3 = expectations(
        load_fixture,
        input_mint=SOL_MINT,
        output_mint=USDC_MINT,
        in_base=200_000_000,
        min_out_base=1,
        max_lamport_drop=200_000_000 + LAMPORT_FEE_ALLOWANCE,
        input_account=WSOL_ATA,
        output_account=USDC_ATA,
    )
    assert "output rise" in check_simulation(sim_from_fixture(sell), exp3)  # type: ignore[operator]


# --------------------------------------------------------------------------- raw layouts


def raw_token_account(
    mint: str, owner: str, amount: int, delegate: str | None = None, close: str | None = None
) -> bytes:
    b = bytes(PK(mint)) + bytes(PK(owner)) + struct.pack("<Q", amount)
    b += (struct.pack("<I", 1) + bytes(PK(delegate))) if delegate else (struct.pack("<I", 0) + bytes(32))
    b += bytes([1]) + struct.pack("<I", 0) + struct.pack("<Q", 0) + struct.pack("<Q", 0)
    b += (struct.pack("<I", 1) + bytes(PK(close))) if close else (struct.pack("<I", 0) + bytes(32))
    return b


def test_parse_token_account_bytes() -> None:
    acc = parse_token_account_bytes(raw_token_account(USDC_MINT, WALLET, 42, delegate=FOREIGN, close=WALLET))
    assert acc == {
        "mint": USDC_MINT,
        "owner": WALLET,
        "amount": 42,
        "delegate": FOREIGN,
        "delegated_amount": 0,
        "state": "initialized",
        "is_native": False,
        "close_authority": WALLET,
        "extensions": [],
    }
    with pytest.raises(ValueError):
        parse_token_account_bytes(b"\x00" * 100)
    # token-2022 account with ImmutableOwner extension (type 7, len 0)
    ext = raw_token_account(USDC_MINT, WALLET, 1) + bytes([2]) + struct.pack("<HH", 7, 0)
    assert parse_token_account_bytes(ext)["extensions"][0]["name"] == "immutableOwner"


def test_parse_mint_bytes_with_extensions() -> None:
    base = (
        struct.pack("<I", 0)
        + bytes(32)
        + struct.pack("<Q", 10)
        + bytes([6, 1])
        + struct.pack("<I", 0)
        + bytes(32)
    )
    plain = parse_mint_bytes(base)
    assert plain["decimals"] == 6 and plain["mintAuthority"] is None and plain["extensions"] == []
    # padded to 165 + type byte Mint + TLV: permanentDelegate (12, 32 bytes) + defaultAccountState frozen (6, 1 byte)
    tlv = struct.pack("<HH", 12, 32) + bytes(PK(FOREIGN)) + struct.pack("<HH", 6, 1) + bytes([2])
    ext = base + bytes(165 - 82) + bytes([1]) + tlv
    parsed = parse_mint_bytes(ext)
    names = [e["extension"] for e in parsed["extensions"]]
    assert names == ["permanentDelegate", "defaultAccountState"]
    assert parsed["extensions"][1]["state"] == {"accountState": "frozen"}
    account = {"owner": TOKEN_2022_PROGRAM, "data": [base64.b64encode(ext).decode(), "base64"]}
    assert check_mint(account) == "mint: token-2022 extension permanentDelegate"


# --------------------------------------------------------------------------- mint table


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("rpc/mint_spl", None),
        ("rpc/mint_spl_sol", None),
        ("rpc/mint_spl_base64", None),
        ("rpc/mint_t2022_clean", None),
        ("rpc/mint_t2022_default_initialized", None),
        ("rpc/mint_missing", "not found"),
        ("rpc/mint_not_token_program", "not a token program"),
        ("rpc/mint_spl_mint_authority", "mint authority present"),
        ("rpc/mint_spl_freeze_authority", "freeze authority present"),
        ("rpc/mint_t2022_transfer_fee", "transferFeeConfig"),
        ("rpc/mint_t2022_transfer_hook", "transferHook"),
        ("rpc/mint_t2022_permanent_delegate", "permanentDelegate"),
        ("rpc/mint_t2022_non_transferable", "nonTransferable"),
        ("rpc/mint_t2022_default_frozen", "defaultAccountState frozen"),
        ("rpc/mint_t2022_pausable", "pausable"),
        ("rpc/mint_t2022_confidential", "confidentialTransferMint"),
        ("rpc/mint_t2022_close_authority", "mintCloseAuthority"),
    ],
)
def test_check_mint_table(load_fixture: Callable[[str], Any], fixture: str, expected: str | None) -> None:
    value = load_fixture(fixture)["result"]["value"]
    reason = check_mint(value)
    if expected is None:
        assert reason is None
    else:
        assert reason is not None and expected in reason


def test_mint_decimals(load_fixture: Callable[[str], Any]) -> None:
    assert mint_decimals(load_fixture("rpc/mint_spl")["result"]["value"]) == 6
    assert mint_decimals(load_fixture("rpc/mint_spl_sol")["result"]["value"]) == 9
    assert mint_decimals(load_fixture("rpc/mint_spl_base64")["result"]["value"]) == 6
    with pytest.raises(ValueError):
        mint_decimals(load_fixture("rpc/mint_not_token_program")["result"]["value"])

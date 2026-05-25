import re

import base58

SOLANA_B58_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")

PUMP_URL_RE = re.compile(r"pump\.fun/(?:coin/)?([1-9A-HJ-NP-Za-km-z]{32,44})", re.IGNORECASE)
DEXSCREENER_URL_RE = re.compile(
    r"dexscreener\.com/solana/([1-9A-HJ-NP-Za-km-z]{32,44})", re.IGNORECASE
)
BIRDEYE_URL_RE = re.compile(r"birdeye\.so/token/([1-9A-HJ-NP-Za-km-z]{32,44})", re.IGNORECASE)
GMGN_URL_RE = re.compile(r"gmgn\.ai/sol/token/([1-9A-HJ-NP-Za-km-z]{32,44})", re.IGNORECASE)

URL_PATTERNS = [PUMP_URL_RE, DEXSCREENER_URL_RE, BIRDEYE_URL_RE, GMGN_URL_RE]


def is_valid_solana_address(s: str) -> bool:
    try:
        decoded = base58.b58decode(s)
    except Exception:
        return False
    return len(decoded) == 32


def extract_candidates(text: str) -> list[str]:
    """Return de-duped, validated Solana addresses from arbitrary text (tweet body + expanded URLs)."""
    found: list[str] = []

    for pat in URL_PATTERNS:
        for m in pat.finditer(text):
            found.append(m.group(1))

    for m in SOLANA_B58_RE.finditer(text):
        found.append(m.group(0))

    seen: set[str] = set()
    result: list[str] = []
    for cand in found:
        if cand in seen:
            continue
        if not is_valid_solana_address(cand):
            continue
        seen.add(cand)
        result.append(cand)
    return result

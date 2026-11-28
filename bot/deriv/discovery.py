"""Dynamic Deriv symbol discovery and 12-channel market-group routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from bot.config.settings import SymbolProfile
from bot.deriv.client import DerivAPIClient

_HZ_VOL_RE = re.compile(r"^1HZ(\d+)V$", re.IGNORECASE)
_STD_VOL_RE = re.compile(r"^R_(\d+)$", re.IGNORECASE)
_BOOM_RE = re.compile(r"^BOOM(\d+)N?$", re.IGNORECASE)
_CRASH_RE = re.compile(r"^CRASH(\d+)N?$", re.IGNORECASE)
_JUMP_RE = re.compile(r"^JD(\d+)$", re.IGNORECASE)
_RANGE_RE = re.compile(r"^RB(\d+)$", re.IGNORECASE)
_BASKET_RE = re.compile(r"^WLD([A-Z]{3})$", re.IGNORECASE)
_FRX_RE = re.compile(r"^FRX([A-Z]{3})([A-Z]{3})$", re.IGNORECASE)
_CRYPTO_RE = re.compile(r"^CRY([A-Z]{2,6})USD$", re.IGNORECASE)

_CANONICAL_SYMBOL_NAMES: dict[str, str] = {
    "OTC_DJI": "Wall Street 30",
    "OTC_NDX": "US Tech 100",
    "OTC_SPC": "US 500",
    "OTC_AS51": "Australia 200",
    "OTC_HSI": "Hong Kong 50",
    "OTC_N225": "Japan 225",
    "OTC_AEX": "Netherlands 25",
    "OTC_FCHI": "France 40",
    "OTC_FTSE": "UK 100",
    "OTC_GDAXI": "Germany 40",
    "OTC_SSMI": "Swiss 20",
    "OTC_SX5E": "Euro 50",
    "RDBEAR": "Bear Market Index",
    "RDBULL": "Bull Market Index",
    "STPRNG": "Step Index 100",
    "STPRNG2": "Step Index 200",
    "STPRNG3": "Step Index 300",
    "STPRNG4": "Step Index 400",
    "STPRNG5": "Step Index 500",
    "FRXXAUUSD": "Gold/USD",
    "FRXXAGUSD": "Silver/USD",
    "FRXXPDUSD": "Palladium/USD",
    "FRXXPTUSD": "Platinum/USD",
    "WLDXAU": "Gold Basket",
}


def normalize_symbol_description(symbol: str, raw_name: str = "") -> str:
    """Return a clean, unambiguous Deriv instrument name across all asset classes."""
    sym = (symbol or "").strip().upper()
    if sym in _CANONICAL_SYMBOL_NAMES:
        return _CANONICAL_SYMBOL_NAMES[sym]
    if m := _HZ_VOL_RE.match(sym):
        return f"Volatility {m.group(1)} (1s) Index"
    if m := _STD_VOL_RE.match(sym):
        return f"Volatility {m.group(1)} Index"
    if m := _BOOM_RE.match(sym):
        return f"Boom {m.group(1)} Index"
    if m := _CRASH_RE.match(sym):
        return f"Crash {m.group(1)} Index"
    if m := _JUMP_RE.match(sym):
        return f"Jump {m.group(1)} Index"
    if m := _RANGE_RE.match(sym):
        return f"Range Break {m.group(1)} Index"
    if m := _BASKET_RE.match(sym):
        return f"{m.group(1).upper()} Basket"
    if m := _FRX_RE.match(sym):
        return f"{m.group(1).upper()}/{m.group(2).upper()}"
    if m := _CRYPTO_RE.match(sym):
        return f"{m.group(1).upper()}/USD"
    cleaned = " ".join((raw_name or "").split()).strip()
    return cleaned or symbol


@dataclass(frozen=True)
class SubmarketRouting:
    group_name: str
    slack_channel: str
    reddit_flair: str
    primary_timeframe: int
    higher_timeframe: int | None
    lower_timeframe: int | None
    atr_stop_multiple: float
    trailing_factor: float
    multiplier: float
    spread_bps: float
    slippage_bps: float


SUBMARKET_ROUTING: dict[str, SubmarketRouting] = {
    "random_index": SubmarketRouting(
        group_name="Volatility Indices",
        slack_channel="C0CJWBD99QD",
        reddit_flair="📈 #volatility-indices",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=2.0,
        trailing_factor=1.25,
        multiplier=40.0,
        spread_bps=3.0,
        slippage_bps=1.5,
    ),
    "crash_index": SubmarketRouting(
        group_name="Crash & Boom",
        slack_channel="C0CJWBE5E2Z",
        reddit_flair="💥 #crash-boom",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=2.2,
        trailing_factor=1.30,
        multiplier=25.0,
        spread_bps=4.0,
        slippage_bps=2.0,
    ),
    "step_index": SubmarketRouting(
        group_name="Step Indices",
        slack_channel="C0CJWBERH4M",
        reddit_flair="🪜 #step-indices",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=1.9,
        trailing_factor=1.20,
        multiplier=50.0,
        spread_bps=2.5,
        slippage_bps=1.5,
    ),
    "jump_index": SubmarketRouting(
        group_name="Jump Indices",
        slack_channel="C0CK3GTA76V",
        reddit_flair="🦘 #jump-indices",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=2.3,
        trailing_factor=1.35,
        multiplier=20.0,
        spread_bps=4.5,
        slippage_bps=2.5,
    ),
    "range_index": SubmarketRouting(
        group_name="Range Break",
        slack_channel="C0CKW1NCZR6",
        reddit_flair="📦 #range-break",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=2.0,
        trailing_factor=1.25,
        multiplier=30.0,
        spread_bps=3.5,
        slippage_bps=2.0,
    ),
    "range_break": SubmarketRouting(
        group_name="Range Break",
        slack_channel="C0CKW1NCZR6",
        reddit_flair="📦 #range-break",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=2.0,
        trailing_factor=1.25,
        multiplier=30.0,
        spread_bps=3.5,
        slippage_bps=2.0,
    ),
    "random_daily": SubmarketRouting(
        group_name="Daily Reset",
        slack_channel="C0CJWBH79RT",
        reddit_flair="🔄 #daily-reset",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=2.0,
        trailing_factor=1.25,
        multiplier=40.0,
        spread_bps=3.0,
        slippage_bps=1.5,
    ),
    "forex_basket": SubmarketRouting(
        group_name="FX Baskets",
        slack_channel="C0CJZM5LS4E",
        reddit_flair="🧺 #fx-baskets",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=1.9,
        trailing_factor=1.20,
        multiplier=50.0,
        spread_bps=2.0,
        slippage_bps=1.0,
    ),
    "commodity_basket": SubmarketRouting(
        group_name="Metals & Commodities",
        slack_channel="C0CK5BEUT1S",
        reddit_flair="🥇 #metals",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=2.0,
        trailing_factor=1.25,
        multiplier=50.0,
        spread_bps=3.0,
        slippage_bps=1.5,
    ),
    "metals": SubmarketRouting(
        group_name="Metals & Commodities",
        slack_channel="C0CK5BEUT1S",
        reddit_flair="🥇 #metals",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=2.0,
        trailing_factor=1.25,
        multiplier=50.0,
        spread_bps=3.0,
        slippage_bps=1.5,
    ),
    "energy": SubmarketRouting(
        group_name="Metals & Commodities",
        slack_channel="C0CK5BEUT1S",
        reddit_flair="🥇 #metals",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=2.0,
        trailing_factor=1.25,
        multiplier=50.0,
        spread_bps=3.0,
        slippage_bps=1.5,
    ),
    "major_pairs": SubmarketRouting(
        group_name="Forex Majors",
        slack_channel="C0CJWBHVAMT",
        reddit_flair="💱 #forex-majors",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=1.8,
        trailing_factor=1.20,
        multiplier=100.0,
        spread_bps=1.5,
        slippage_bps=1.0,
    ),
    "minor_pairs": SubmarketRouting(
        group_name="Forex Minors",
        slack_channel="C0CKW1QGPEU",
        reddit_flair="🌐 #forex-minors",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=1.9,
        trailing_factor=1.25,
        multiplier=100.0,
        spread_bps=2.5,
        slippage_bps=1.5,
    ),
    "non_stable_coin": SubmarketRouting(
        group_name="Crypto",
        slack_channel="C0CK3GYBY7K",
        reddit_flair="₿ #crypto-signals",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=2.1,
        trailing_factor=1.30,
        multiplier=25.0,
        spread_bps=5.0,
        slippage_bps=2.5,
    ),
    "americas_OTC": SubmarketRouting(
        group_name="Stock Indices",
        slack_channel="C0CK1ERLNNN",
        reddit_flair="📊 #stock-indices",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=2.0,
        trailing_factor=1.25,
        multiplier=50.0,
        spread_bps=3.0,
        slippage_bps=1.5,
    ),
    "asia_oceania_OTC": SubmarketRouting(
        group_name="Stock Indices",
        slack_channel="C0CK1ERLNNN",
        reddit_flair="📊 #stock-indices",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=2.0,
        trailing_factor=1.25,
        multiplier=50.0,
        spread_bps=3.0,
        slippage_bps=1.5,
    ),
    "europe_OTC": SubmarketRouting(
        group_name="Stock Indices",
        slack_channel="C0CK1ERLNNN",
        reddit_flair="📊 #stock-indices",
        primary_timeframe=300,
        higher_timeframe=None,
        lower_timeframe=None,
        atr_stop_multiple=2.0,
        trailing_factor=1.25,
        multiplier=50.0,
        spread_bps=3.0,
        slippage_bps=1.5,
    ),
}

_MARKET_FALLBACK_ROUTING: dict[str, SubmarketRouting] = {
    "cryptocurrency": SUBMARKET_ROUTING["non_stable_coin"],
    "indices": SUBMARKET_ROUTING["americas_OTC"],
    "commodities": SUBMARKET_ROUTING["metals"],
    "forex": SUBMARKET_ROUTING["minor_pairs"],
    "synthetic_index": SUBMARKET_ROUTING["random_index"],
}

_DEFAULT_ROUTING = SubmarketRouting(
    group_name="General Markets",
    slack_channel="C09C9FGM8QY",
    reddit_flair="🌐 #trading · Pulse & EOD",
    primary_timeframe=300,
    higher_timeframe=None,
    lower_timeframe=None,
    atr_stop_multiple=2.0,
    trailing_factor=1.25,
    multiplier=50.0,
    spread_bps=3.0,
    slippage_bps=1.5,
)


def routing_for(market: str, submarket: str) -> SubmarketRouting:
    if submarket in SUBMARKET_ROUTING:
        return SUBMARKET_ROUTING[submarket]
    return _MARKET_FALLBACK_ROUTING.get(market, _DEFAULT_ROUTING)


def parse_active_symbols(
    raw_symbols: list[dict[str, Any]],
    overrides: dict[str, SymbolProfile] | None = None,
) -> dict[str, SymbolProfile]:
    """Convert Deriv active_symbols response into routed SymbolProfiles."""
    result: dict[str, SymbolProfile] = {}
    override_map = overrides or {}

    for item in raw_symbols:
        if int(item.get("is_trading_suspended", 0)) == 1:
            continue
        symbol = str(item.get("underlying_symbol") or item.get("symbol") or "").strip()
        if not symbol:
            continue

        market = str(item.get("market") or "synthetic_index")
        submarket = str(item.get("submarket") or "random_index")
        name = normalize_symbol_description(
            symbol,
            str(item.get("underlying_symbol_name") or item.get("display_name") or symbol),
        )
        is_open = int(item.get("exchange_is_open", 1)) == 1
        route = routing_for(market, submarket)

        if symbol in override_map:
            base = override_map[symbol]
            resolved_name = normalize_symbol_description(symbol, name or base.description)
            result[symbol] = base.model_copy(
                update={
                    "enabled": True,
                    "description": resolved_name,
                    "market": market,
                    "submarket": submarket,
                    "exchange_is_open": is_open,
                    "slack_channel": base.slack_channel or route.slack_channel,
                    "reddit_flair": base.reddit_flair or route.reddit_flair,
                }
            )
        else:
            result[symbol] = SymbolProfile(
                symbol=symbol,
                enabled=True,
                description=name,
                market=market,
                submarket=submarket,
                exchange_is_open=is_open,
                slack_channel=route.slack_channel,
                reddit_flair=route.reddit_flair,
                primary_timeframe=route.primary_timeframe,
                higher_timeframe=route.higher_timeframe,
                lower_timeframe=route.lower_timeframe,
                multiplier=route.multiplier,
                atr_stop_multiple=route.atr_stop_multiple,
                trailing_factor=route.trailing_factor,
                min_confidence=0.58,
                cooldown_bars=0,
                max_exposure=1,
                spread_bps=route.spread_bps,
                slippage_bps=route.slippage_bps,
            )
    return result


async def discover_active_symbols(
    client: DerivAPIClient,
    overrides: dict[str, SymbolProfile] | None = None,
) -> dict[str, SymbolProfile]:
    """Fetch all active symbols from Deriv v1 WebSocket (no product_type field)."""
    resp = await client.request({"active_symbols": "brief"})
    raw = resp.get("active_symbols", [])
    return parse_active_symbols(raw, overrides=overrides)

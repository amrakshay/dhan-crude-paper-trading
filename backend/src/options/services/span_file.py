"""NSE Clearing's daily SPAN risk-parameter file, parsed into what margin needs.

The file (`nsccl.YYYYMMDD.s.spn`, SPAN 4.00 XML, ~43 MB unzipped) is the
EXCHANGE's own statement of risk: for every futures and options contract a
16-scenario RISK ARRAY -- the loss one LONG unit takes under each price and
volatility move -- plus, per underlying ("combined commodity"), the calendar
spread charges and the short-option minimum. A broker's SPAN figure starts
from exactly these numbers; nothing here estimates them.

Sign convention, verified on the 2026-11-23 file: a POSITIVE array value is a
LOSS to a long position. A NIFTY future's scenario 3 (price up a third of the
scan range) reads -781.09 -- a gain -- and its scenario 4 the same.

Only what margin needs is kept: per contract its price, composite delta and
array; per underlying its spread definitions, short-option minimum rate and
the underlying's own settlement price. Parsed once per file with `iterparse`
(a full DOM of 43 MB would be ~1 GB) and cached beside the zip as a pickle, so
a restart re-reads ~5 MB rather than re-parsing.

Known simplifications, stated rather than hidden:
  * only the FIRST risk-array tier (`<ra><r>1</r>`) is read -- NSE publishes
    one tier per contract;
  * spread rates are read as flat charges per matched unit of delta
    (`chargeMeth F`), which is every NSE equity-derivatives spread in the
    2026-11-23 file; another method is reported, not guessed at;
  * inter-commodity credits are not applied (NSE's equity derivatives file
    defines none for single underlyings).
"""
import os
import pickle
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

SCENARIOS = 16
PARSER_VERSION = 1

# (underlying, "F"/"C"/"P", expiry YYYYMMDD, strike as a normalised string)
ContractKey = Tuple[str, str, str, str]


def strike_key(value) -> str:
    """One spelling for a strike: '25350', '1402.5'. The file writes
    '25350.00'; the database holds Decimal('25350.0000')."""
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


@dataclass(frozen=True)
class RiskEntry:
    price: float
    delta: float                    # composite delta, per unit
    array: Tuple[float, ...]        # 16 scenario losses, per LONG unit


@dataclass(frozen=True)
class Spread:
    """A calendar spread: legs A and B on opposite sides, charged per unit."""
    rate: float
    method: str
    legs: Tuple[Tuple[str, str, float], ...]   # (expiry, side A/B, ratio)


@dataclass
class SpanData:
    business_date: date
    created: str
    contracts: Dict[ContractKey, RiskEntry] = field(default_factory=dict)
    spreads: Dict[str, List[Spread]] = field(default_factory=dict)
    som_rate: Dict[str, float] = field(default_factory=dict)
    underlying_price: Dict[str, float] = field(default_factory=dict)
    price_scan: Dict[str, float] = field(default_factory=dict)
    unsupported: List[str] = field(default_factory=list)
    parser_version: int = PARSER_VERSION

    def entry(self, underlying: str, right: str, expiry: date, strike=None) -> Optional[RiskEntry]:
        key = (
            underlying,
            right,
            expiry.strftime("%Y%m%d"),
            strike_key(strike) if strike is not None else "",
        )
        return self.contracts.get(key)


def _text(element, tag: str) -> Optional[str]:
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else None


def _risk_array(element) -> Optional[Tuple[Tuple[float, ...], float]]:
    ra = element.find("ra")
    if ra is None:
        return None
    values = [float(a.text) for a in ra.findall("a")]
    if len(values) != SCENARIOS:
        return None
    delta_text = _text(ra, "d")
    return tuple(values), float(delta_text) if delta_text else 0.0


def parse(path: str, business_date: date) -> SpanData:
    """Parse one .spn file. Streams; never holds the whole tree."""
    data = SpanData(business_date=business_date, created="")
    context = ET.iterparse(path, events=("end",))
    for _event, element in context:
        tag = element.tag
        if tag == "created" and not data.created:
            data.created = (element.text or "").strip()
        elif tag == "phyPf":
            code = _text(element, "pfCode")
            phy = element.find("phy")
            if code and phy is not None and _text(phy, "p"):
                data.underlying_price[code] = float(_text(phy, "p"))
            element.clear()
        elif tag == "futPf":
            code = _text(element, "pfCode")
            for fut in element.findall("fut"):
                parsed = _risk_array(fut)
                if code and parsed:
                    data.contracts[(code, "F", _text(fut, "pe"), "")] = RiskEntry(
                        float(_text(fut, "p") or 0), parsed[1], parsed[0]
                    )
                scan = fut.find("scanRate")
                if code and scan is not None and _text(scan, "priceScan"):
                    data.price_scan.setdefault(code, float(_text(scan, "priceScan")))
            element.clear()
        elif tag == "oopPf":
            code = _text(element, "pfCode")
            for series in element.findall("series"):
                expiry = _text(series, "pe")
                for opt in series.findall("opt"):
                    parsed = _risk_array(opt)
                    right = _text(opt, "o")
                    strike = _text(opt, "k")
                    if code and parsed and right in ("C", "P") and strike:
                        data.contracts[(code, right, expiry, strike_key(strike))] = RiskEntry(
                            float(_text(opt, "p") or 0), parsed[1], parsed[0]
                        )
            element.clear()
        elif tag == "ccDef":
            code = _text(element, "cc")
            spreads: List[Spread] = []
            for spread in element.findall("dSpread"):
                method = _text(spread, "chargeMeth") or ""
                rate = spread.find("rate")
                legs = tuple(
                    (_text(leg, "pe"), _text(leg, "rs"), float(_text(leg, "i") or 1))
                    for leg in spread.findall("pLeg")
                )
                if method != "F":
                    data.unsupported.append(f"{code}: spread method {method!r}")
                    continue
                spreads.append(Spread(float(_text(rate, "val") or 0), method, legs))
            if code:
                data.spreads[code] = spreads
                som = element.find("somTiers/tier/rate/val")
                data.som_rate[code] = float(som.text) if som is not None and som.text else 0.0
            element.clear()
    return data


def load(spn_path: str, business_date: date) -> SpanData:
    """Parse, or read the pickle cached beside the file if it is current."""
    cache = spn_path + ".pkl"
    if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(spn_path):
        try:
            with open(cache, "rb") as handle:
                cached = pickle.load(handle)
            if getattr(cached, "parser_version", None) == PARSER_VERSION:
                return cached
        except Exception:  # noqa: BLE001 - a bad cache is re-parsed, not fatal
            pass
    data = parse(spn_path, business_date)
    try:
        with open(cache, "wb") as handle:
            pickle.dump(data, handle, protocol=pickle.HIGHEST_PROTOCOL)
    except OSError:
        pass
    return data

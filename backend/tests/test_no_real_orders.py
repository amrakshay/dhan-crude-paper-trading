"""HARD SAFETY TEST -- this build must never be able to place a real order.

This project trades paper rupees. The Dhan credentials it holds are for MARKET
DATA ONLY: the WebSocket feed, the option chain endpoint and the instrument
master CSV. This test fails the build if anything resembling a broker
order-placement, order-modify, order-cancel, funds or holdings path appears in
the source.

It is deliberately strict about *where* it looks:

  * Comments and docstrings are exempt -- documentation is allowed to name the
    endpoints it is refusing to call. Everything else (string literals,
    identifiers, imports, frontend source) is in scope.
  * Bare paths like "/orders" are NOT banned globally, because this application
    has its own `/api/orders` surface. They are banned inside the Dhan client
    modules, where a relative endpoint would be resolved against Dhan's base
    URL.
  * Any string mentioning Dhan's hosts must match an explicit allowlist of
    market-data URLs.
"""
import ast
import os
import re
from pathlib import Path
from typing import Iterator, List, Tuple

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent
FRONTEND_ROOT = PROJECT_ROOT / "frontend"

# This file necessarily contains the forbidden strings.
SELF = Path(__file__).resolve()

EXCLUDED_DIR_NAMES = {
    ".venv", "venv", "node_modules", "__pycache__", ".git", "dist", "build",
    ".pytest_cache", "data", ".mypy_cache", ".vite",
}

# --- 1. Dhan hosts: only these exact market-data URLs may ever appear -------
ALLOWED_DHAN_URLS = {
    "https://api.dhan.co/v2",                                    # REST base
    "https://api.dhan.co/v2/optionchain",                        # greeks / IV
    "https://api.dhan.co/v2/optionchain/expirylist",             # expiry list
    # Candle history for the price chart. Read-only market data, the same
    # category as the option chain: no order, funds or holdings surface is
    # reachable through either. Added 2026-09-16 with src/market/services/
    # dhan_charts_client.py, which is the only module allowed to name them.
    "https://api.dhan.co/v2/charts/historical",                  # daily candles
    "https://api.dhan.co/v2/charts/intraday",                    # intraday candles
    "wss://api-feed.dhan.co",                                    # live feed
    "https://images.dhan.co/api-data/api-scrip-master-detailed.csv",  # master
}
DHAN_HOST_PATTERN = re.compile(r"(?:api\.dhan\.co|api-feed\.dhan\.co|images\.dhan\.co)")

# --- 2. Relative endpoints that must never appear in a Dhan client module ---
# These are DhanHQ v2's trading / account surface.
FORBIDDEN_DHAN_ENDPOINTS = [
    "/orders",
    "/superorder",
    "/forever",
    "/funds",
    "/fundlimit",
    "/holdings",
    "/positions",
    "/edis",
    "/killswitch",
    "/margincalculator",
    "/slicing",
    "/tradebook",
]
# Files that talk to Dhan. A relative endpoint string here would be joined onto
# the Dhan base URL, so the ban applies.
DHAN_CLIENT_PATTERNS = ("dhan_", "market/services/")
ALLOWED_DHAN_ENDPOINTS = {"/optionchain", "/optionchain/expirylist"}

# --- 3. Broker order/account operations, banned everywhere -----------------
FORBIDDEN_IDENTIFIERS = {
    "place_order", "modify_order", "cancel_order", "place_slice_order",
    "cancel_all_orders", "place_forever", "modify_forever", "cancel_forever",
    "place_super_order", "modify_super_order", "cancel_super_order",
    "get_fund_limits", "get_holdings", "convert_position", "get_trade_book",
    "get_order_list", "get_order_by_id", "get_order_by_correlation_id",
    "kill_switch", "expiry_margin", "margin_calculator",
}

# --- 4. The official SDK bundles order placement; it must not be imported ---
FORBIDDEN_IMPORTS = {"dhanhq", "dhan_http", "DhanContext", "dhanhq.dhanhq"}


def _iter_source_files(root: Path, suffixes: Tuple[str, ...]) -> Iterator[Path]:
    if not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIR_NAMES]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix in suffixes and path.resolve() != SELF:
                yield path


def _docstring_nodes(tree: ast.AST) -> set:
    """Identity set of Constant nodes that are docstrings."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def _python_string_literals(path: Path) -> List[Tuple[int, str]]:
    """Every string literal in a Python file, excluding docstrings.

    Comments never reach the AST at all, so they are exempt for free.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = _docstring_nodes(tree)
    literals = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            literals.append((node.lineno, node.value))
    return literals


def _is_dhan_client_module(path: Path) -> bool:
    relative = str(path.relative_to(BACKEND_ROOT)).replace(os.sep, "/")
    return any(marker in relative for marker in DHAN_CLIENT_PATTERNS)


def test_no_dhan_host_outside_allowlist():
    """Every URL pointing at Dhan must be one of the market-data endpoints."""
    violations = []

    for path in _iter_source_files(BACKEND_ROOT, (".py",)):
        for lineno, literal in _python_string_literals(path):
            if DHAN_HOST_PATTERN.search(literal) and literal not in ALLOWED_DHAN_URLS:
                violations.append(f"{path}:{lineno}: {literal!r}")

    for path in _iter_source_files(
        BACKEND_ROOT, (".yaml", ".yml", ".json", ".ini", ".env", ".example")
    ):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            code = line.split("#", 1)[0]
            if not DHAN_HOST_PATTERN.search(code):
                continue
            if not any(allowed in code for allowed in ALLOWED_DHAN_URLS):
                violations.append(f"{path}:{lineno}: {line.strip()!r}")

    for path in _iter_source_files(FRONTEND_ROOT, (".js", ".jsx", ".ts", ".tsx")):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if DHAN_HOST_PATTERN.search(line):
                violations.append(f"{path}:{lineno}: {line.strip()!r}")

    assert not violations, (
        "Dhan URL outside the market-data allowlist:\n  " + "\n  ".join(violations)
    )


def test_no_trading_endpoints_in_dhan_clients():
    """No broker trading/account endpoint string inside a Dhan client module."""
    violations = []
    for path in _iter_source_files(BACKEND_ROOT, (".py",)):
        if not _is_dhan_client_module(path):
            continue
        for lineno, literal in _python_string_literals(path):
            stripped = literal.strip()
            if stripped in ALLOWED_DHAN_ENDPOINTS:
                continue
            for endpoint in FORBIDDEN_DHAN_ENDPOINTS:
                if stripped == endpoint or stripped.startswith(endpoint + "/"):
                    violations.append(f"{path}:{lineno}: {literal!r}")

    assert not violations, (
        "Broker trading endpoint referenced from a Dhan client module:\n  "
        + "\n  ".join(violations)
    )


def test_no_broker_order_operations_anywhere():
    """No identifier, attribute or string naming a broker order operation."""
    violations = []
    for path in _iter_source_files(BACKEND_ROOT, (".py",)):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        docstrings = _docstring_nodes(tree)

        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in FORBIDDEN_IDENTIFIERS:
                violations.append(f"{path}:{node.lineno}: name {node.id!r}")
            elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_IDENTIFIERS:
                violations.append(f"{path}:{node.lineno}: attribute {node.attr!r}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in FORBIDDEN_IDENTIFIERS:
                    violations.append(f"{path}:{node.lineno}: def {node.name!r}")
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and node.value.strip() in FORBIDDEN_IDENTIFIERS
            ):
                violations.append(f"{path}:{node.lineno}: string {node.value!r}")

    assert not violations, (
        "Broker order operation referenced in source:\n  " + "\n  ".join(violations)
    )


def test_dhanhq_sdk_is_not_imported():
    """The official SDK ships order placement; it must not be in the tree."""
    violations = []
    for path in _iter_source_files(BACKEND_ROOT, (".py",)):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_IMPORTS:
                        violations.append(f"{path}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").split(".")[0]
                if module in FORBIDDEN_IMPORTS:
                    violations.append(f"{path}:{node.lineno}: from {node.module} import")

    assert not violations, (
        "The dhanhq SDK must not be imported:\n  " + "\n  ".join(violations)
    )


def test_dhanhq_is_not_a_declared_dependency():
    requirements = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
    declared = [
        line.split("#", 1)[0].strip()
        for line in requirements.splitlines()
        if line.split("#", 1)[0].strip()
    ]
    offenders = [line for line in declared if re.match(r"^dhanhq\b", line, re.I)]
    assert not offenders, f"dhanhq must not be a dependency: {offenders}"


def test_safety_scanner_actually_catches_violations(tmp_path):
    """Guard against the scanner silently passing because it scans nothing.

    A test that greps for patterns is worthless if the grep is broken, so this
    feeds it a known-bad file and asserts it is detected.
    """
    bad = tmp_path / "bad_module.py"
    bad.write_text(
        'BASE = "https://api.dhan.co/v2/orders"\n'
        "def place_order(payload):\n"
        "    return payload\n",
        encoding="utf-8",
    )

    literals = _python_string_literals(bad)
    assert any(
        DHAN_HOST_PATTERN.search(value) and value not in ALLOWED_DHAN_URLS
        for _, value in literals
    ), "URL scanner failed to flag a forbidden Dhan order URL"

    tree = ast.parse(bad.read_text(encoding="utf-8"))
    flagged = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in FORBIDDEN_IDENTIFIERS
    ]
    assert flagged == ["place_order"], "identifier scanner failed to flag place_order"


def test_scanner_covers_a_meaningful_number_of_files():
    """If the walk breaks, every test above passes vacuously."""
    scanned = list(_iter_source_files(BACKEND_ROOT, (".py",)))
    assert len(scanned) >= 10, f"Only {len(scanned)} python files scanned; walk is broken"

"""Outbound hosts: every one of them, named in exactly one module.

`tests/test_no_real_orders.py` guards Dhan's hosts and is deliberately left
untouched by this change -- Telegram is not a broker and must not become one,
and widening that file would blur what it is for.

This is the sibling for the general case. Until 2026-09-18 every external call
this application made was INBOUND market data: it fetched prices and sent
nothing anywhere. Telegram is the first outbound path and the first inbound
CONTROL path, so "which third parties can this process talk to" became a
question worth being able to answer by reading one list.

The rule is the one `dhan_token_client.py` already follows for `/RenewToken`:
one module names the host, and a test asserts nothing else does. A regex that
accepts a family of hosts is not the same guarantee as a set you can read in
one glance.
"""
import ast
import os
import re
from pathlib import Path
from typing import Iterator, List, Tuple

BACKEND_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = BACKEND_ROOT / "src"
SELF = Path(__file__).resolve()

EXCLUDED_DIR_NAMES = {
    ".venv", "venv", "node_modules", "__pycache__", ".git", "dist", "build",
    ".pytest_cache", "data", ".mypy_cache", ".vite",
}

# Every third party this application may contact, and the ONE module allowed to
# name each one. Dhan's own allowlist lives in test_no_real_orders.py and is
# not duplicated here; this is the answer to "what ELSE".
OUTBOUND_HOSTS = {
    "api.telegram.org": "telegram_client.py",
    # The IPO dashboard's GMP source. The page a human reads is at
    # www.investorgain.com, but the table on it is built client-side from this
    # JSON host -- established by fetching the page on 2026-09-19, not assumed.
    # Only the host that is actually CALLED is listed: this file answers "what
    # can this process talk to", and a host listed here but never contacted
    # would weaken that answer. The display link is built in the browser.
    "webnodejs.investorgain.com": "ipo_source_client.py",
}

# Anything that looks like an external http(s) URL in a string literal.
_URL = re.compile(r"https?://([A-Za-z0-9.-]+)")

# Hosts that are not third parties: this machine, and the documentation links
# that appear in prose and in configuration comments.
_LOCAL = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "test",
}


def _iter_python_files(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIR_NAMES]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix == ".py" and path.resolve() != SELF:
                yield path


def _docstring_nodes(tree: ast.AST) -> set:
    found = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def _executable_string_literals(path: Path) -> List[Tuple[int, str]]:
    """Every string literal except docstrings.

    Docstrings are exempt for the same reason they are in the no-real-orders
    scanner: documentation is allowed to name what it is refusing to call, and
    comments never reach the AST at all.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = _docstring_nodes(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_only_the_telegram_client_names_the_bot_api():
    """`api.telegram.org` appears in one module, and the bot token is in its PATH.

    Containment is the point: "what can this application send, and to whom" has
    to be answerable by reading one file. It matters more here than for a
    read-only endpoint, because a second module composing a bot URL is a second
    place the token can be logged.
    """
    naming = {}
    for path in _iter_python_files(SRC_ROOT):
        for lineno, literal in _executable_string_literals(path):
            for host, owner in OUTBOUND_HOSTS.items():
                if host in literal:
                    naming.setdefault(host, set()).add(path.name)

    for host, owner in OUTBOUND_HOSTS.items():
        found = naming.get(host, set())
        assert found <= {owner}, (
            f"{host} must be named in {owner} alone, found in {sorted(found)}"
        )


def test_only_the_ipo_source_client_names_the_gmp_host():
    """One module, one third party -- the general rule, not a Telegram one.

    The test above already enforces this for every entry in OUTBOUND_HOSTS;
    this one names the IPO source so that a change which quietly moved the URL
    into a service or a route fails with a message about the IPO source rather
    than a generic one.
    """
    naming = set()
    for path in _iter_python_files(SRC_ROOT):
        for _lineno, literal in _executable_string_literals(path):
            if "webnodejs.investorgain.com" in literal:
                naming.add(path.name)

    assert naming <= {"ipo_source_client.py"}, (
        "The IPO GMP host must be named in ipo_source_client.py alone, found "
        f"in {sorted(naming)}"
    )


def test_the_ipo_client_declares_a_closed_set_of_urls():
    """A set, not a pattern. Same rule the Dhan clients follow."""
    from src.ipo.services import ipo_source_client

    assert ipo_source_client.IPO_SOURCE_HOST == "webnodejs.investorgain.com"
    assert ipo_source_client.GMP_REPORT_URL.startswith(
        "https://webnodejs.investorgain.com/cloud/v2/report/data-read/331/"
    )


def test_the_telegram_client_declares_a_closed_set_of_methods():
    """A closed set, not a pattern.

    The same rule the Dhan clients follow: a regex that accepts a family of
    methods is not the guarantee a set you can read in one glance is.
    """
    from src.connections.services import telegram_client

    assert telegram_client.ALLOWED_METHODS == frozenset(
        {"getMe", "getChat", "sendMessage", "getUpdates"}
    )

    client = telegram_client.TelegramClient("123:abc")
    with __import__("pytest").raises(telegram_client.TelegramError):
        client._url("deleteWebhook")  # noqa: SLF001


def test_no_unlisted_external_host_appears_in_an_executable_string():
    """A new third party fails the build until somebody lists it on purpose.

    Dhan's hosts are governed by test_no_real_orders.py's own allowlist and are
    skipped here rather than listed twice; everything else must be in
    OUTBOUND_HOSTS.
    """
    from tests.test_no_real_orders import DHAN_HOST_PATTERN

    violations = []
    for path in _iter_python_files(SRC_ROOT):
        for lineno, literal in _executable_string_literals(path):
            for match in _URL.finditer(literal):
                host = match.group(1)
                if host in _LOCAL or DHAN_HOST_PATTERN.search(host):
                    continue
                if host in OUTBOUND_HOSTS:
                    continue
                violations.append(f"{path}:{lineno}: {host}")

    assert not violations, (
        "An external host that is on no allowlist:\n  " + "\n  ".join(violations)
    )


def test_the_scanner_actually_catches_an_unlisted_host(tmp_path):
    """A grep-based test is worthless if the grep is broken."""
    bad = tmp_path / "bad_module.py"
    bad.write_text('URL = "https://hooks.example-webhook.net/post"\n', encoding="utf-8")

    literals = _executable_string_literals(bad)
    hosts = [
        match.group(1)
        for _lineno, value in literals
        for match in _URL.finditer(value)
    ]
    assert hosts == ["hooks.example-webhook.net"]
    assert hosts[0] not in OUTBOUND_HOSTS


def test_the_scanner_covers_a_meaningful_number_of_files():
    """If the walk breaks, every test above passes vacuously."""
    scanned = list(_iter_python_files(SRC_ROOT))
    assert len(scanned) >= 50, f"Only {len(scanned)} files scanned; the walk is broken"

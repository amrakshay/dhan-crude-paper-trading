"""Assert the three claims this experiment makes about itself.

The root CLAUDE.md's one rule is that this system must never be able to place a
real order, and the application enforces it with `tests/test_no_real_orders.py`.
This experiment sits outside `backend/` and is therefore outside that scanner's
reach, so it makes the equivalent claims here and checks them:

  1. NOTHING IN THE APPLICATION IMPORTS THIS. It is research, not a component.
  2. THE APPLICATION'S DATABASE IS OPENED READ-ONLY, always with mode=ro.
  3. THERE IS NO BROKER SURFACE -- no order, funds or holdings endpoint, no
     order operation name, no dhanhq SDK. Not even a stub.

Run before believing the README's framing:

    python3 scripts/check_isolation.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parent.parent
BACKEND = REPO / "backend"

# Mirrors test_no_real_orders.py's list. Comments and docstrings are exempt --
# documentation may name the endpoints it is refusing to call -- so the check
# runs over the AST, not the text.
BANNED_NAMES = {
    "place_order", "modify_order", "cancel_order", "place_slice_order",
    "get_order_list", "get_order_by_id", "get_fund_limits", "get_holdings",
    "get_positions", "convert_position", "place_forever", "modify_forever",
    "cancel_forever",
}
BANNED_URL = re.compile(
    r"https?://[^\s\"']*dhan\.co[^\s\"']*/(orders?|funds|holdings|positions|"
    r"trades|super/orders|forever)", re.I)
ALLOWED_HOSTS = {
    "api.dhan.co",            # charts/historical only, checked below
    "images.dhan.co",         # the public instrument master
    "ipocentral.in",          # the IPO calendar
}
ALLOWED_DHAN_PATHS = {"/charts/historical"}


def python_files() -> list[Path]:
    return sorted(p for p in HERE.rglob("*.py")
                  if ".venv" not in p.parts and "__pycache__" not in p.parts)


def check_not_imported() -> list[str]:
    problems = []
    if not BACKEND.exists():
        return ["backend/ not found -- cannot check claim 1"]
    for path in BACKEND.rglob("*.py"):
        if ".venv" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\bimport\s+ipolib\b|\bfrom\s+ipolib\b|ipo.breakout", text):
            problems.append(f"{path.relative_to(REPO)} references this experiment")
    return problems


def check_readonly() -> list[str]:
    problems = []
    for path in python_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"paper_trading\.db", text):
            line = text[:match.start()].count("\n") + 1
            window = text[max(0, match.start() - 220): match.end() + 80]
            if "mode=ro" not in window:
                problems.append(
                    f"{path.relative_to(HERE)}:{line} opens the application's "
                    f"database without mode=ro")
    return problems


def check_no_broker_surface() -> list[str]:
    problems = []
    for path in python_files():
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            problems.append(f"{path.relative_to(HERE)}: will not parse ({exc})")
            continue

        for node in ast.walk(tree):
            # Names and attributes, but never a docstring or comment.
            if isinstance(node, ast.Name) and node.id in BANNED_NAMES:
                problems.append(f"{path.relative_to(HERE)}:{node.lineno} "
                                f"uses the broker operation name {node.id!r}")
            if isinstance(node, ast.Attribute) and node.attr in BANNED_NAMES:
                problems.append(f"{path.relative_to(HERE)}:{node.lineno} "
                                f"uses the broker operation name {node.attr!r}")
            if isinstance(node, ast.FunctionDef) and node.name in BANNED_NAMES:
                problems.append(f"{path.relative_to(HERE)}:{node.lineno} "
                                f"defines {node.name!r}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = ([a.name for a in node.names]
                         + ([node.module] if isinstance(node, ast.ImportFrom)
                            and node.module else []))
                for name in names:
                    if name and name.split(".")[0] == "dhanhq":
                        problems.append(f"{path.relative_to(HERE)}:{node.lineno} "
                                        f"imports the dhanhq SDK")
            # String literals only -- a URL in a docstring is documentation.
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if BANNED_URL.search(node.value):
                    problems.append(f"{path.relative_to(HERE)}:{node.lineno} "
                                    f"names a broker trading URL")
                for host in re.findall(r"https?://([^/\s\"']+)", node.value):
                    if (host.endswith("dhan.co") or "investorgain" in host
                            or "ipocentral" in host) and host not in ALLOWED_HOSTS:
                        problems.append(f"{path.relative_to(HERE)}:{node.lineno} "
                                        f"names an un-allowlisted host {host!r}")
    return problems


def check_dhan_paths() -> list[str]:
    """Only /charts/historical may be constructed against api.dhan.co."""
    problems = []
    for path in python_files():
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                match = re.match(r"https?://api\.dhan\.co(/v\d+)?(/\S*)?$",
                                 node.value.strip())
                if match and (match.group(2) or "") not in ALLOWED_DHAN_PATHS:
                    problems.append(
                        f"{path.relative_to(HERE)}:{node.lineno} builds a Dhan "
                        f"URL that is not in {sorted(ALLOWED_DHAN_PATHS)}: "
                        f"{node.value}")
    return problems


def main() -> int:
    checks = [
        ("nothing in backend/ imports this experiment", check_not_imported),
        ("the application's database is opened read-only", check_readonly),
        ("no broker surface exists, not even a stub", check_no_broker_surface),
        ("only /charts/historical is reachable on api.dhan.co", check_dhan_paths),
    ]
    failed = 0
    for label, check in checks:
        problems = check()
        if problems:
            failed += 1
            print(f"FAIL  {label}")
            for problem in problems:
                print(f"        {problem}")
        else:
            print(f"ok    {label}")
    if failed:
        print(f"\n{failed} of {len(checks)} claims do not hold.")
        return 1
    print(f"\nall {len(checks)} claims hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

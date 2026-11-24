"""The shared options engine names no strategy (OPTIONS_TRADER_PLAN.md D7).

`src/options/` serves every NSE option position, whichever module holds it.
The day it imports the Options Trader page, or spells a strategy key, an
automated module built on it later inherits the page's assumptions without
anyone deciding that. A docstring or comment may mention a module; code may not.
"""
import ast
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent / "src" / "options"
FORBIDDEN_IMPORT_PREFIXES = ("src.options_trader",)
FORBIDDEN_LITERALS = ("nse-options-trader",)


def _code_strings(tree: ast.AST):
    """Every string constant that is not a docstring."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant
            ):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield node


def test_the_engine_exists():
    assert any(ENGINE.rglob("*.py"))


def test_the_engine_imports_no_module_package():
    violations = []
    for path in ENGINE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith(FORBIDDEN_IMPORT_PREFIXES):
                    violations.append(f"{path}:{node.lineno}: imports {name}")
    assert violations == []


def test_the_engine_spells_no_strategy_key():
    violations = []
    for path in ENGINE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _code_strings(tree):
            if any(literal in node.value for literal in FORBIDDEN_LITERALS):
                violations.append(f"{path}:{node.lineno}: {node.value!r}")
    assert violations == []

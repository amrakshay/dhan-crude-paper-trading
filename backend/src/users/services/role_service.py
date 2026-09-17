"""Role -> visible pages, loaded from `conf/role-pages.json`.

Kept in a JSON file rather than in code so the mapping can be adjusted without
a deploy, and so the sidebar, the client-side routes and the API all read the
same source.

**This is presentation, not authorisation.** It decides what the UI offers. The
API enforces the same rules independently -- `require_admin` on the settings
and user-management endpoints -- because hiding a nav item stops nobody from
calling the endpoint directly.
"""
import json
import os
import threading
from typing import Any, Dict, List, Optional

from src import config_utils
from src.constants import UserRole
from src.logging_config import get_logger

logger = get_logger("users.roles")

ROLE_PAGES_FILE = "role-pages.json"

# Used when the file is missing or unreadable. Deliberately the LEAST
# privileged shape that still leaves the app usable: everything a plain user
# gets, and Settings only for an admin. A misplaced config file must not
# silently hand out the admin surface.
FALLBACK_PAGES: Dict[str, List[str]] = {
    UserRole.ACCOUNT_ADMIN.value: [
        "/live", "/chain", "/positions", "/orders", "/portfolios", "/reports",
        "/notes", "/strategies", "/users", "/profile", "/settings",
    ],
    UserRole.USER.value: [
        "/live", "/chain", "/positions", "/orders", "/portfolios", "/reports",
        "/notes", "/users", "/profile",
    ],
}

_CACHE: Optional[Dict[str, Any]] = None
_LOCK = threading.Lock()


def role_pages_path() -> str:
    return os.path.join(config_utils.get_config_path(), ROLE_PAGES_FILE)


def load_role_pages(force: bool = False) -> Dict[str, Any]:
    """Load (once) and return the parsed mapping."""
    global _CACHE

    with _LOCK:
        if _CACHE is not None and not force:
            return _CACHE

        path = role_pages_path()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
            roles = document.get("roles") or {}
            if not roles:
                raise ValueError("no 'roles' object in the file")
            _CACHE = document
            logger.info(
                "Loaded role page mapping from %s: %s",
                path, {name: len(spec.get("pages") or []) for name, spec in roles.items()},
            )
        except Exception:
            logger.exception(
                "Could not read %s; falling back to the built-in mapping. The "
                "sidebar will still be correct, and the API enforces access "
                "independently either way.",
                path,
            )
            _CACHE = {
                "roles": {
                    name: {"label": name, "pages": list(pages)}
                    for name, pages in FALLBACK_PAGES.items()
                }
            }
        return _CACHE


def reload_role_pages() -> Dict[str, Any]:
    return load_role_pages(force=True)


def pages_for_role(role: str) -> List[str]:
    """Pages this role may see, given what is switched on.

    Effective pages are **role pages INTERSECT enabled-feature pages**. One
    intersection, computed here, and the sidebar, the client-side routes and
    anything else reading `/auth/me` follow from it -- there is deliberately no
    second gating mechanism in the frontend.

    Only pages that a strategy or capability actually grants are gated. A page
    outside that set (Positions, Order History, Profile, Users, Settings) is
    never withdrawn by a toggle: history must stay readable when a strategy is
    switched off, and locking an admin out of Settings because a capability was
    disabled would be absurd.

    This is still presentation. The API refuses what the role may not do, and a
    disabled feature's endpoints refuse on their own.
    """
    roles = load_role_pages().get("roles") or {}
    spec = roles.get(role)
    if spec is None:
        logger.warning(
            "No page mapping for role %r; granting no pages. Add it to %s.",
            role, role_pages_path(),
        )
        return []

    granted = list(spec.get("pages") or [])
    try:
        from src.strategies.services.strategy_registry import (
            StrategyRegistry,
            get_strategy_registry,
        )

        gated = StrategyRegistry.gated_pages()
        available = get_strategy_registry().feature_pages()
    except Exception:  # noqa: BLE001 - never lock the UI out over a config error
        logger.exception(
            "Could not read the strategy registry; showing every page this role "
            "has rather than hiding pages because of a configuration error"
        )
        return granted

    return [page for page in granted if page not in gated or page in available]


def can_access_page(role: str, page: str) -> bool:
    return page in pages_for_role(role)


def role_catalogue() -> List[Dict[str, Any]]:
    """Every role with its label and pages, for the Users page's dropdown."""
    roles = load_role_pages().get("roles") or {}
    return [
        {
            "role": name,
            "label": spec.get("label") or name,
            "description": spec.get("description") or "",
            "pages": list(spec.get("pages") or []),
        }
        for name, spec in roles.items()
    ]

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
        "/live", "/chain", "/positions", "/orders", "/reports", "/notes",
        "/users", "/profile", "/settings",
    ],
    UserRole.USER.value: [
        "/live", "/chain", "/positions", "/orders", "/reports", "/notes",
        "/users", "/profile",
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
    """Pages this role may see. An unknown role gets nothing."""
    roles = load_role_pages().get("roles") or {}
    spec = roles.get(role)
    if spec is None:
        logger.warning(
            "No page mapping for role %r; granting no pages. Add it to %s.",
            role, role_pages_path(),
        )
        return []
    return list(spec.get("pages") or [])


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

"""User management: creation, updates, deletion and the guard rails.

Every rule that protects the application from being locked out of itself is
enforced HERE, server-side, not by hiding a button:

* the seeded administrator can never be deleted, demoted or deactivated;
* nobody can delete themselves, whatever their role;
* an email address is never updated -- it is the login identifier and the thing
  every audit line refers to;
* the last active administrator cannot be removed by any route.

The seeded administrator rule is the one that is easy to get wrong. Blocking
only the delete would still let an admin demote that row to ROLE_USER while it
remained undeletable, leaving a system with no administrator and no way back.
"""
from typing import List, Optional

from src.constants import UserRole, UserStatus
from src.core.time_utils import utc_now
from src.logging_config import get_logger
from src.users.database.db_models.user_model import User
from src.users.database.db_operations.user_repository import (
    UserRepository,
    normalise_email,
)
from src.users.services import password_service
from src.users.services.password_service import PasswordError

logger = get_logger("users.service")

SEED_USER_EMAIL = "trader@abc.com"
SEED_USER_FIRST_NAME = "Paper"
SEED_USER_LAST_NAME = "Trader"

MAX_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 255


class UserValidationError(Exception):
    """A rejected user operation, with a reason safe to show the caller."""


class UserService:
    def __init__(self, repository: UserRepository):
        self.repository = repository

    # --- validation --------------------------------------------------------
    @staticmethod
    def _require_text(value: Optional[str], label: str, limit: int) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise UserValidationError(f"{label} is required")
        if len(cleaned) > limit:
            raise UserValidationError(f"{label} must be at most {limit} characters")
        return cleaned

    @staticmethod
    def _validate_email(email: Optional[str]) -> str:
        cleaned = normalise_email(email)
        if not cleaned:
            raise UserValidationError("Email is required")
        if len(cleaned) > MAX_EMAIL_LENGTH:
            raise UserValidationError(
                f"Email must be at most {MAX_EMAIL_LENGTH} characters"
            )
        # Deliberately shallow. Anything stricter rejects valid addresses, and
        # there is no delivery step here to be protected by a stricter rule.
        local, _, domain = cleaned.partition("@")
        if not local or not domain or "." not in domain or " " in cleaned:
            raise UserValidationError(f"{email!r} is not a valid email address")
        return cleaned

    @staticmethod
    def _validate_role(role: Optional[str]) -> str:
        cleaned = (role or "").strip().upper()
        valid = {item.value for item in UserRole}
        if cleaned not in valid:
            raise UserValidationError(
                f"role must be one of {', '.join(sorted(valid))}, got {role!r}"
            )
        return cleaned

    @staticmethod
    def _validate_status(status: Optional[str]) -> str:
        cleaned = (status or "").strip().upper()
        valid = {item.value for item in UserStatus}
        if cleaned not in valid:
            raise UserValidationError(
                f"status must be one of {', '.join(sorted(valid))}, got {status!r}"
            )
        return cleaned

    @staticmethod
    def _hash(password: Optional[str]) -> str:
        try:
            return password_service.hash_password(password or "")
        except PasswordError as exc:
            raise UserValidationError(str(exc)) from exc

    # --- reading -----------------------------------------------------------
    async def list_users(self) -> List[User]:
        return await self.repository.list_users()

    async def get_user(self, user_id: int) -> User:
        user = await self.repository.get_by_id(user_id)
        if user is None:
            raise UserValidationError(f"User {user_id} not found")
        return user

    # --- creation ----------------------------------------------------------
    async def create_user(
        self,
        email: str,
        first_name: str,
        last_name: str,
        password: str,
        role: str,
        status: str = UserStatus.ACTIVE.value,
        must_change_password: bool = False,
    ) -> User:
        email = self._validate_email(email)
        first_name = self._require_text(first_name, "First name", MAX_NAME_LENGTH)
        last_name = self._require_text(last_name, "Last name", MAX_NAME_LENGTH)
        role = self._validate_role(role)
        status = self._validate_status(status)
        password_hash = self._hash(password)

        if await self.repository.get_by_email(email) is not None:
            raise UserValidationError(f"A user with email {email} already exists")

        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            password_hash=password_hash,
            role=role,
            status=status,
            must_change_password=bool(must_change_password),
            is_seed_user=False,
            password_changed_at=utc_now(),
        )
        self.repository.session.add(user)
        await self.repository.session.flush()
        logger.info(
            "User %s created: %s (%s, %s, must_change_password=%s)",
            user.id, email, role, status, user.must_change_password,
        )
        return user

    # --- updates -----------------------------------------------------------
    async def update_user(
        self,
        user_id: int,
        acting_user_id: int,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        role: Optional[str] = None,
        status: Optional[str] = None,
        password: Optional[str] = None,
        must_change_password: Optional[bool] = None,
    ) -> User:
        """Update a user. `email` is absent by design -- it can never change."""
        user = await self.get_user(user_id)

        if first_name is not None:
            user.first_name = self._require_text(
                first_name, "First name", MAX_NAME_LENGTH
            )
        if last_name is not None:
            user.last_name = self._require_text(last_name, "Last name", MAX_NAME_LENGTH)

        if role is not None:
            new_role = self._validate_role(role)
            if new_role != user.role:
                await self._guard_role_change(user, new_role)
                logger.info(
                    "User %s (%s) role %s -> %s by user %s",
                    user.id, user.email, user.role, new_role, acting_user_id,
                )
                user.role = new_role

        if status is not None:
            new_status = self._validate_status(status)
            if new_status != user.status:
                await self._guard_status_change(user, new_status, acting_user_id)
                logger.info(
                    "User %s (%s) status %s -> %s by user %s",
                    user.id, user.email, user.status, new_status, acting_user_id,
                )
                user.status = new_status

        if password:
            user.password_hash = self._hash(password)
            user.password_changed_at = utc_now()
            # An admin setting someone else's password hands over a temporary
            # one; the owner picks their own on next sign-in unless told not to.
            if must_change_password is None and user_id != acting_user_id:
                user.must_change_password = True
            logger.info(
                "Password reset for user %s (%s) by user %s",
                user.id, user.email, acting_user_id,
            )

        if must_change_password is not None:
            user.must_change_password = bool(must_change_password)

        await self.repository.session.flush()
        logger.info("User %s (%s) updated by user %s", user.id, user.email, acting_user_id)
        return user

    async def update_own_profile(
        self,
        user_id: int,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> User:
        """The profile page. Name only -- not role, status or email."""
        user = await self.get_user(user_id)
        if first_name is not None:
            user.first_name = self._require_text(
                first_name, "First name", MAX_NAME_LENGTH
            )
        if last_name is not None:
            user.last_name = self._require_text(last_name, "Last name", MAX_NAME_LENGTH)
        await self.repository.session.flush()
        logger.info("User %s updated their own profile", user_id)
        return user

    async def change_own_password(
        self, user_id: int, current_password: str, new_password: str
    ) -> User:
        """Change your own password, proving you know the current one."""
        user = await self.get_user(user_id)

        if not password_service.verify_password(current_password, user.password_hash):
            logger.warning(
                "User %s (%s) gave the wrong current password when changing it",
                user.id, user.email,
            )
            raise UserValidationError("Current password is incorrect")

        if password_service.verify_password(new_password, user.password_hash):
            raise UserValidationError(
                "The new password must be different from the current one"
            )

        user.password_hash = self._hash(new_password)
        user.password_changed_at = utc_now()
        # Clearing this is what releases the first-login gate.
        user.must_change_password = False
        await self.repository.session.flush()
        logger.info("User %s (%s) changed their own password", user.id, user.email)
        return user

    # --- deletion ----------------------------------------------------------
    async def delete_user(self, user_id: int, acting_user_id: int) -> None:
        user = await self.get_user(user_id)

        if user.is_seed_user:
            raise UserValidationError(
                "The default administrator cannot be deleted. Deactivating or "
                "deleting it would leave the application with no way in."
            )
        if user.id == acting_user_id:
            raise UserValidationError(
                "You cannot delete your own account. Ask another administrator."
            )
        if (
            user.role == UserRole.ACCOUNT_ADMIN.value
            and user.status == UserStatus.ACTIVE.value
            and await self.repository.count_active_admins(excluding_id=user.id) == 0
        ):
            raise UserValidationError(
                "This is the last active administrator; deleting it would leave "
                "nobody able to administer the application."
            )

        await self.repository.session.delete(user)
        await self.repository.session.flush()
        logger.info(
            "User %s (%s) deleted by user %s", user_id, user.email, acting_user_id
        )

    # --- guard rails -------------------------------------------------------
    async def _guard_role_change(self, user: User, new_role: str) -> None:
        if user.is_seed_user:
            # Not in the original brief, and the hole it closes is real: the
            # seeded admin is undeletable, so demoting it would strand the
            # application with an administrator slot that cannot be refilled.
            raise UserValidationError(
                "The default administrator's role cannot be changed. It is also "
                "undeletable, so demoting it would leave no way to administer "
                "the application."
            )
        if (
            user.role == UserRole.ACCOUNT_ADMIN.value
            and new_role != UserRole.ACCOUNT_ADMIN.value
            and await self.repository.count_active_admins(excluding_id=user.id) == 0
        ):
            raise UserValidationError(
                "This is the last active administrator; demoting it would leave "
                "nobody able to administer the application."
            )

    async def _guard_status_change(
        self, user: User, new_status: str, acting_user_id: int
    ) -> None:
        if new_status == UserStatus.ACTIVE.value:
            return
        if user.is_seed_user:
            raise UserValidationError(
                "The default administrator cannot be deactivated, for the same "
                "reason it cannot be deleted."
            )
        if user.id == acting_user_id:
            raise UserValidationError(
                "You cannot deactivate your own account -- it would end your own "
                "session on the next request."
            )
        if (
            user.role == UserRole.ACCOUNT_ADMIN.value
            and await self.repository.count_active_admins(excluding_id=user.id) == 0
        ):
            raise UserValidationError(
                "This is the last active administrator; deactivating it would "
                "leave nobody able to administer the application."
            )

    # --- seeding -----------------------------------------------------------
    async def ensure_seed_user(self, password: Optional[str]) -> Optional[User]:
        """Create the default administrator if it is not there. Idempotent.

        Called from the application's startup and from the migration, so a
        database reaches a usable state whether it was built by
        `alembic upgrade head` or by `create_tables()`.

        An existing seed user is left alone -- in particular its password is
        NOT reset from the environment on every boot, or changing it in the UI
        would be undone by the next restart.
        """
        existing = await self.repository.get_seed_user()
        if existing is not None:
            logger.debug(
                "Seed administrator already present (id=%s, %s)",
                existing.id, existing.email,
            )
            return existing

        by_email = await self.repository.get_by_email(SEED_USER_EMAIL)
        if by_email is not None:
            # Someone created it by hand. Adopt it rather than failing on the
            # unique index.
            by_email.is_seed_user = True
            await self.repository.session.flush()
            logger.info(
                "Adopted the existing %s account as the seeded administrator",
                SEED_USER_EMAIL,
            )
            return by_email

        if not password:
            logger.error(
                "APP_ADMIN_PASSWORD is not set, so the default administrator "
                "(%s) was not created and nobody can log in. Set it in .env and "
                "restart.",
                SEED_USER_EMAIL,
            )
            return None

        try:
            password_hash = self._hash(password)
        except UserValidationError as exc:
            logger.error(
                "APP_ADMIN_PASSWORD is unusable (%s), so the default "
                "administrator was not created.", exc,
            )
            return None

        user = User(
            email=SEED_USER_EMAIL,
            first_name=SEED_USER_FIRST_NAME,
            last_name=SEED_USER_LAST_NAME,
            password_hash=password_hash,
            role=UserRole.ACCOUNT_ADMIN.value,
            status=UserStatus.ACTIVE.value,
            # Deliberately false: the default credential is allowed to stand.
            # Recorded as a known risk in the README rather than forced.
            must_change_password=False,
            is_seed_user=True,
            password_changed_at=utc_now(),
        )
        self.repository.session.add(user)
        await self.repository.session.flush()
        logger.info(
            "Seeded the default administrator %s (%s) from APP_ADMIN_PASSWORD",
            SEED_USER_EMAIL, UserRole.ACCOUNT_ADMIN.value,
        )
        return user

    async def record_login(self, user: User) -> None:
        user.last_login_at = utc_now()
        await self.repository.session.flush()

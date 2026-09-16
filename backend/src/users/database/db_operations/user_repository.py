"""User persistence. All queries for the users table live here."""
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import UserRole, UserStatus
from src.core.base_repository import BaseRepository
from src.users.database.db_models.user_model import User


def normalise_email(email: Optional[str]) -> str:
    """The canonical stored form: trimmed and lowercased.

    Emails are compared case-insensitively, and doing that by normalising on
    the way in rather than with a SQL `lower()` keeps the unique index usable
    and behaves the same on SQLite and MySQL, whose default collations differ.
    """
    return (email or "").strip().lower()


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, User)

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.session.execute(
            select(User).where(User.email == normalise_email(email))
        )
        return result.scalar_one_or_none()

    async def list_users(self) -> List[User]:
        """Every user, admins first then alphabetically. No pagination: this is
        a single-operator tool with a handful of accounts."""
        result = await self.session.execute(
            select(User).order_by(User.role.asc(), User.first_name.asc(), User.id.asc())
        )
        return list(result.scalars().all())

    async def get_seed_user(self) -> Optional[User]:
        result = await self.session.execute(
            select(User).where(User.is_seed_user.is_(True)).order_by(User.id.asc())
        )
        return result.scalars().first()

    async def count_users(self) -> int:
        result = await self.session.execute(select(func.count()).select_from(User))
        return int(result.scalar_one())

    async def count_active_admins(self, excluding_id: Optional[int] = None) -> int:
        """Active administrators, optionally ignoring one row.

        Used to refuse the change that would leave the application with nobody
        who can administer it.
        """
        query = select(func.count()).select_from(User).where(
            User.role == UserRole.ACCOUNT_ADMIN.value,
            User.status == UserStatus.ACTIVE.value,
        )
        if excluding_id is not None:
            query = query.where(User.id != excluding_id)
        result = await self.session.execute(query)
        return int(result.scalar_one())

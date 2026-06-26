import asyncio

from loguru import logger
from passlib.context import CryptContext
from sqlalchemy import select

from app.infrastructure.database.models import User, UserQuotaUsage, UserRole
from app.infrastructure.database.session import async_session_factory
from config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def create_admin_user() -> None:
    settings = get_settings()
    admin_email = settings.admin.email
    admin_password = settings.admin.password

    if not admin_email or not admin_password:
        logger.error("ADMIN_EMAIL and ADMIN_PASSWORD must be set in environment")
        raise SystemExit(1)

    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.email == admin_email))
        existing_user = result.scalar_one_or_none()

        if existing_user is not None:
            logger.info("Admin user with email {} already exists", admin_email)
            return

        user = User(
            email=admin_email,
            password_hash=pwd_context.hash(admin_password),
            role=UserRole.ADMIN,
            is_active=True,
        )
        session.add(user)
        await session.flush()

        quota_usage = UserQuotaUsage(
            user_id=user.id,
            total_bytes=0,
            private_bytes=0,
            photos_bytes=0,
        )
        session.add(quota_usage)
        await session.commit()

        logger.info("Created admin user with email {} and role ADMIN", admin_email)


def main() -> None:
    asyncio.run(create_admin_user())


if __name__ == "__main__":
    main()

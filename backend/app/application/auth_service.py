from datetime import UTC, datetime, timedelta
from uuid import UUID

from jose import JWTError, jwt
from loguru import logger
from passlib.context import CryptContext

from app.domain.entities.user import User
from app.domain.exceptions import EmailAlreadyExistsError, InvalidCredentialsError
from app.infrastructure.database.repositories.user_repo import UserRepository
from config import Settings, get_settings

JWT_ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:
    def __init__(
        self,
        user_repo: UserRepository,
        settings: Settings | None = None,
    ) -> None:
        self._user_repo = user_repo
        self._settings = settings or get_settings()

    async def register(self, email: str, password: str) -> User:
        logger.info("Registration attempt for email {}", email)
        existing_user = await self._user_repo.get_by_email(email)
        if existing_user is not None:
            logger.warning("Registration failed: email {} already exists", email)
            raise EmailAlreadyExistsError(f"User with email {email} already exists")

        password_hash = pwd_context.hash(password)
        user = await self._user_repo.create(email=email, password_hash=password_hash)
        logger.info("User {} registered successfully", user.id)
        return user

    async def login(self, email: str, password: str) -> str:
        logger.info("Login attempt for email {}", email)
        user = await self._user_repo.get_by_email(email)
        if user is None or user.password_hash is None:
            logger.warning("Login failed: invalid credentials for email {}", email)
            raise InvalidCredentialsError("Invalid email or password")

        if not pwd_context.verify(password, user.password_hash):
            logger.warning("Login failed: invalid credentials for email {}", email)
            raise InvalidCredentialsError("Invalid email or password")

        if not user.is_active:
            logger.warning("Login failed: user {} is inactive", user.id)
            raise InvalidCredentialsError("Invalid email or password")

        token = self._create_access_token(user.id)
        logger.info("User {} logged in successfully", user.id)
        return token

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        return await self._user_repo.get_by_id(user_id)

    async def get_user_from_token(self, token: str) -> User | None:
        user_id = self._decode_token_user_id(token)
        if user_id is None:
            return None
        user = await self.get_user_by_id(user_id)
        if user is None or not user.is_active:
            logger.warning("Token references missing or inactive user {}", user_id)
            return None
        return user

    def _create_access_token(self, user_id: UUID) -> str:
        ttl = self._settings.auth.session_ttl_seconds
        expire = datetime.now(UTC) + timedelta(seconds=ttl)
        payload = {"sub": str(user_id), "exp": expire}
        return jwt.encode(
            payload,
            self._settings.auth.jwt_secret,
            algorithm=JWT_ALGORITHM,
        )

    def _decode_token_user_id(self, token: str) -> UUID | None:
        try:
            payload = jwt.decode(
                token,
                self._settings.auth.jwt_secret,
                algorithms=[JWT_ALGORITHM],
            )
            return UUID(payload["sub"])
        except (JWTError, ValueError, KeyError):
            logger.warning("Failed to decode access token")
            return None

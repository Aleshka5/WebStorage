from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from loguru import logger

from config import Settings, get_settings

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_OAUTH_SCOPES = "openid email profile"


@dataclass(frozen=True)
class GoogleUserInfo:
    email: str
    google_id: str
    name: str


class GoogleOAuthClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def get_auth_url(self, state: str) -> str:
        auth_settings = self._settings.auth
        params = {
            "client_id": auth_settings.google_client_id,
            "redirect_uri": auth_settings.google_redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_OAUTH_SCOPES,
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
        url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
        logger.info("Generated Google OAuth authorization URL")
        return url

    async def exchange_code(self, code: str, state: str) -> GoogleUserInfo:
        auth_settings = self._settings.auth
        logger.info("Exchanging Google OAuth authorization code")

        token_payload = {
            "code": code,
            "client_id": auth_settings.google_client_id,
            "client_secret": auth_settings.google_client_secret,
            "redirect_uri": auth_settings.google_redirect_uri,
            "grant_type": "authorization_code",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            token_response = await client.post(GOOGLE_TOKEN_URL, data=token_payload)
            if token_response.status_code != 200:
                logger.error(
                    "Google token exchange failed with status {}",
                    token_response.status_code,
                )
                token_response.raise_for_status()

            access_token = token_response.json()["access_token"]

            userinfo_response = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if userinfo_response.status_code != 200:
                logger.error(
                    "Google userinfo request failed with status {}",
                    userinfo_response.status_code,
                )
                userinfo_response.raise_for_status()

            data = userinfo_response.json()

        if not data.get("email") or not data.get("sub"):
            logger.error("Google userinfo response is missing required fields")
            raise ValueError("Incomplete Google user profile")

        user_info = GoogleUserInfo(
            email=data["email"],
            google_id=data["sub"],
            name=data.get("name", data["email"]),
        )
        logger.info("Google OAuth code exchanged successfully for email {}", user_info.email)
        return user_info

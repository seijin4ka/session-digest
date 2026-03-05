import os


class AppConfig:
    def __init__(self):
        self._user_api_key: str | None = None

    @property
    def api_key(self) -> str | None:
        return self._user_api_key or os.environ.get("OPENAI_API_KEY")

    @property
    def has_env_key(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    @property
    def has_any_key(self) -> bool:
        return bool(self.api_key)

    @property
    def source(self) -> str | None:
        if self._user_api_key:
            return "web"
        if os.environ.get("OPENAI_API_KEY"):
            return "env"
        return None

    def set_user_key(self, key: str) -> None:
        self._user_api_key = key

    def clear_user_key(self) -> None:
        self._user_api_key = None


app_config = AppConfig()

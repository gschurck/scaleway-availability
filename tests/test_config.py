from __future__ import annotations

from app.config import Settings


def test_settings_do_not_load_dotenv_implicitly(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("APP_NAME=Loaded from dotenv\n")

    settings = Settings()

    assert settings.app_name == "Metal Availability"


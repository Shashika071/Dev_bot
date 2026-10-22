from app.config import Settings


def test_shared_compose_env_allows_frontend_keys_but_validates_backend_settings(tmp_path):
    env = tmp_path / "shared.env"
    env.write_text("BOT_MODE=synthetic\nVITE_API_URL=http://localhost:8000\nPOSTGRES_HOST=postgres\n", encoding="utf-8")
    config = Settings(_env_file=env)
    assert config.bot_mode == "synthetic"
    assert not hasattr(config, "vite_api_url")

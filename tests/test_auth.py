import json
import pytest
from unittest.mock import MagicMock, patch
from tgtg_bot.auth import authenticate, MATCHING_USER_AGENT

class TestAuthenticate:
    def test_authenticate_with_explicit_email(self, tmp_path, sample_credentials):
        tokens_file = tmp_path / "tokens.json"

        with patch("tgtg_bot.auth.TgtgClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.get_credentials.return_value = sample_credentials
            mock_client_cls.return_value = mock_instance

            creds = authenticate(tokens_path=tokens_file, email="user@example.com")

            mock_client_cls.assert_called_once_with(email="user@example.com", user_agent=MATCHING_USER_AGENT)
            mock_instance.get_credentials.assert_called_once()
            assert creds == sample_credentials

            # Check that tokens were written to the file
            assert tokens_file.exists()
            saved_data = json.loads(tokens_file.read_text())
            assert saved_data == sample_credentials

    def test_authenticate_with_env_var_email(self, monkeypatch, tmp_path, sample_credentials):
        tokens_file = tmp_path / "tokens.json"
        monkeypatch.setenv("TGTG_EMAIL", "env_user@example.com")

        with patch("tgtg_bot.auth.TgtgClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.get_credentials.return_value = sample_credentials
            mock_client_cls.return_value = mock_instance

            creds = authenticate(tokens_path=tokens_file)

            mock_client_cls.assert_called_once_with(email="env_user@example.com", user_agent=MATCHING_USER_AGENT)
            assert creds == sample_credentials
            assert tokens_file.exists()

    def test_authenticate_with_interactive_input(self, monkeypatch, tmp_path, sample_credentials):
        tokens_file = tmp_path / "tokens.json"
        monkeypatch.delenv("TGTG_EMAIL", raising=False)

        with patch("builtins.input", return_value="prompted@example.com"):
            with patch("tgtg_bot.auth.TgtgClient") as mock_client_cls:
                mock_instance = MagicMock()
                mock_instance.get_credentials.return_value = sample_credentials
                mock_client_cls.return_value = mock_instance

                creds = authenticate(tokens_path=tokens_file)

                mock_client_cls.assert_called_once_with(email="prompted@example.com", user_agent=MATCHING_USER_AGENT)
                assert creds == sample_credentials

    def test_authenticate_missing_email_exits(self, monkeypatch, tmp_path):
        tokens_file = tmp_path / "tokens.json"
        monkeypatch.delenv("TGTG_EMAIL", raising=False)

        with patch("builtins.input", return_value="   "):
            with pytest.raises(SystemExit) as exc_info:
                authenticate(tokens_path=tokens_file)
            assert exc_info.value.code == 1

    def test_authenticate_datadome_captcha_error(self, tmp_path, capsys):
        tokens_file = tmp_path / "tokens.json"

        with patch("tgtg_bot.auth.TgtgClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.get_credentials.side_effect = Exception(
                '(403, b\'{"url":"https://geo.captcha-delivery.com/interstitial/?cid=123"}\')'
            )
            mock_client_cls.return_value = mock_instance

            with pytest.raises(SystemExit) as exc_info:
                authenticate(tokens_path=tokens_file, email="user@example.com")
            assert exc_info.value.code == 1
            captured = capsys.readouterr().out
            assert "DataDome anti-bot challenge encountered" in captured
            assert "https://geo.captcha-delivery.com" in captured

import pytest
import requests
from unittest.mock import MagicMock, patch
from tgtg_bot.notifier import (
    format_notification_message,
    send_whatsapp_message,
    notify_available_items,
    CALLMEBOT_URL,
)

class TestFormatNotificationMessage:
    def test_single_item(self, sample_available_item):
        msg = format_notification_message([sample_available_item])
        assert "Too Good To Go Alert!" in msg
        assert "Found 1 available bag:" in msg
        assert "Artisan Bakery" in msg
        assert "Pastry Surprise Bag" in msg
        assert "Stock: 3 | Price: 4.99 USD" in msg

    def test_multiple_items(self, sample_available_item):
        second_item = {
            "display_name": "Sushi Box",
            "items_available": 2,
            "store": {"store_name": "Tokyo Deli"},
            "item": {"price_including_taxes": {"minor_units": 600, "decimals": 2, "code": "USD"}},
        }
        msg = format_notification_message([sample_available_item, second_item])
        assert "Found 2 available bags:" in msg
        assert "Artisan Bakery" in msg
        assert "Tokyo Deli" in msg

    def test_empty_list(self):
        assert format_notification_message([]) == ""


class TestSendWhatsappMessage:
    @patch("tgtg_bot.notifier.requests.get")
    def test_send_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        success = send_whatsapp_message(
            phone="+1 234 567 8900",
            apikey="secret123",
            message="Hello TGTG!",
        )

        assert success is True
        mock_get.assert_called_once_with(
            CALLMEBOT_URL,
            params={
                "phone": "+12345678900",
                "text": "Hello TGTG!",
                "apikey": "secret123",
            },
            timeout=10,
        )

    @patch("tgtg_bot.notifier.requests.get")
    def test_send_http_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Invalid API Key"
        mock_get.return_value = mock_response

        success = send_whatsapp_message(
            phone="12345678900",
            apikey="invalid_key",
            message="Test",
        )

        assert success is False

    @patch("tgtg_bot.notifier.requests.get")
    def test_send_request_exception(self, mock_get):
        mock_get.side_effect = requests.RequestException("Connection timed out")

        success = send_whatsapp_message(
            phone="12345678900",
            apikey="key",
            message="Test",
        )

        assert success is False

    def test_send_missing_parameters(self):
        assert send_whatsapp_message("", "key", "msg") is False
        assert send_whatsapp_message("123", "", "msg") is False
        assert send_whatsapp_message("123", "key", "") is False


class TestNotifyAvailableItems:
    @patch("tgtg_bot.notifier.send_whatsapp_message", return_value=True)
    def test_notify_with_explicit_credentials(self, mock_send, sample_available_item):
        result = notify_available_items(
            [sample_available_item],
            phone="+123456789",
            apikey="my_apikey",
        )
        assert result is True
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        assert kwargs["phone"] == "+123456789"
        assert kwargs["apikey"] == "my_apikey"
        assert "Artisan Bakery" in kwargs["message"]

    @patch("tgtg_bot.notifier.send_whatsapp_message", return_value=True)
    def test_notify_with_env_credentials(self, mock_send, monkeypatch, sample_available_item):
        monkeypatch.setenv("WHATSAPP_PHONE", "+987654321")
        monkeypatch.setenv("WHATSAPP_APIKEY", "env_apikey")

        result = notify_available_items([sample_available_item])
        assert result is True
        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        assert kwargs["phone"] == "+987654321"
        assert kwargs["apikey"] == "env_apikey"

    def test_notify_skipped_when_credentials_missing(self, monkeypatch, sample_available_item, capsys):
        monkeypatch.delenv("WHATSAPP_PHONE", raising=False)
        monkeypatch.delenv("WHATSAPP_APIKEY", raising=False)
        monkeypatch.delenv("CALLMEBOT_API_KEY", raising=False)

        result = notify_available_items([sample_available_item])
        assert result is False
        captured = capsys.readouterr().out
        assert "WhatsApp notifications skipped" in captured

    def test_notify_skipped_on_empty_items(self):
        result = notify_available_items([], phone="123", apikey="abc")
        assert result is False

from unittest.mock import MagicMock, patch
from tgtg_bot.monitor import main

def test_monitor_main_flow(capsys, sample_available_item, sample_sold_out_item):
    """End-to-end functional test of monitor.main() output and workflow."""
    mock_client = MagicMock()
    mock_client.get_items.return_value = [sample_available_item, sample_sold_out_item]

    with patch("tgtg_bot.monitor.get_client", return_value=mock_client):
        main()

    captured = capsys.readouterr().out
    assert "Checking your favorite stores..." in captured
    assert "Found 2 favorite item(s):" in captured
    assert "Artisan Bakery" in captured
    assert "Pastry Surprise Bag" in captured
    assert "AVAILABLE: 3" in captured
    assert "Green Grocer" in captured
    assert "SOLD OUT" in captured

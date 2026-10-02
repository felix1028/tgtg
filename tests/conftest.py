import pytest

@pytest.fixture
def sample_available_item():
    return {
        "item": {
            "item_id": "12345",
            "name": "Surprise Bag",
            "price_including_taxes": {
                "code": "USD",
                "minor_units": 499,
                "decimals": 2,
            },
        },
        "store": {
            "store_id": "999",
            "store_name": "Artisan Bakery",
        },
        "display_name": "Pastry Surprise Bag",
        "items_available": 3,
    }

@pytest.fixture
def sample_sold_out_item():
    return {
        "item": {
            "item_id": "67890",
            "name": "Produce Box",
            "price_including_taxes": {
                "code": "EUR",
                "minor_units": 350,
                "decimals": 2,
            },
        },
        "store": {
            "store_id": "888",
            "store_name": "Green Grocer",
        },
        "display_name": "Vegetable Box",
        "items_available": 0,
    }

@pytest.fixture
def sample_minimal_item():
    """Item with missing nested structures to test resilience."""
    return {
        "items_available": 1,
    }

@pytest.fixture
def sample_credentials():
    return {
        "access_token": "mock-access-token-123",
        "refresh_token": "mock-refresh-token-456",
        "user_id": "mock-user-id-789",
        "cookie": "mock-cookie-abc",
    }

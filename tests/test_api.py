import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "languages" in data
    assert "legacy_font_support" in data


def test_extract_invalid_extension():
    response = client.post(
        "/api/extract",
        files={"file": ("test.txt", b"content", "text/plain")},
        data={"lang": "auto"},
    )
    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"]


def test_extract_invalid_lang():
    response = client.post(
        "/api/extract",
        files={"file": ("test.png", b"fake", "image/png")},
        data={"lang": "xyz"},
    )
    assert response.status_code == 400
    assert "Invalid language" in response.json()["detail"]

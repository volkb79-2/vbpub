import asyncio
import json
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_basic():
    resp = client.get('/health')
    assert resp.status_code == 200
    data = resp.json()
    assert 'status' in data
    assert 'services' in data

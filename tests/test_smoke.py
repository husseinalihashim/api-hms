import os
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "ci-test-only"
from fastapi.testclient import TestClient
from app.main import app

def test_openapi():
    response = TestClient(app).get("/openapi.json")
    assert response.status_code == 200

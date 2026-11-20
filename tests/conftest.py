"""Shared fixtures. Tests never need network access or an API key:
the embedder is the offline 'hash' one and the LLM is a scriptable fake."""
import json
import os

os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.container import Container, get_container  # noqa: E402
from app.main import app  # noqa: E402
from app.services.seed import DEMO_PASSWORD, seed  # noqa: E402

POLICY_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "policies")


def read_policy(name: str) -> str:
    with open(os.path.join(POLICY_DIR, name), encoding="utf-8") as f:
        return f.read()


class FakeLLM:
    """Scriptable LLM. `responder(question, evidence)` returns raw text or raises."""

    def __init__(self, responder=None):
        self.calls = []
        self.responder = responder or self.cite_first

    @staticmethod
    def cite_first(question, evidence):
        return json.dumps({"answerable": True, "answer": "fake answer", "supporting_chunk_ids": [evidence[0].chunk_id]})

    def generate(self, question, evidence):
        self.calls.append((question, list(evidence)))
        return self.responder(question, evidence)


@pytest.fixture
def make_container(tmp_path):
    def _make(llm=None, seed_data=True, **overrides):
        settings = Settings(
            data_dir=str(tmp_path / "storage"), policies_dir=POLICY_DIR,
            embedding_provider="hash", llm_provider="extractive", **overrides,
        )
        container = Container(settings, llm=llm or FakeLLM())
        if seed_data:
            seed(container)
        return container

    return _make


@pytest.fixture
def container(make_container):
    return make_container()


@pytest.fixture
def client_for():
    """client_for(container) -> TestClient whose app uses that container."""
    def _client(container):
        app.dependency_overrides[get_container] = lambda: container
        return TestClient(app)

    yield _client
    app.dependency_overrides.clear()


@pytest.fixture
def client(container, client_for):
    return client_for(container)


def login(client, username):
    r = client.post("/auth/token", json={"username": username, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture
def headers(client):
    """Auth headers per demo user: headers['acme_employee'], headers['orbit_admin'], ..."""
    return {f"{co}_{role}": login(client, f"{co}_{role}")
            for co in ("acme", "northstar", "orbit") for role in ("employee", "admin")}

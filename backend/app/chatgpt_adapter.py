"""Versioned transport contract for an existing component; no invented live application URL."""

from typing import Protocol
import httpx


class Transport(Protocol):
    def request(self, method: str, path: str, *, json=None): ...


class TrainingAdapter:
    protocol = "xiaohan.training/2"

    def __init__(self, transport: Transport):
        self.transport = transport

    def request(self, method, path, payload=None):
        response = self.transport.request(method, path, json=payload)
        response.raise_for_status()
        return response.json()

    def prepare(self, historical_blueprint=None):
        from app.training_spec_v2 import default_blueprint

        # Only the public default is 60q; an existing 28q integration test
        # may still pass its immutable legacy blueprint explicitly.
        return self.request(
            "POST",
            "/v2/papers",
            historical_blueprint if historical_blueprint is not None else default_blueprint(),
        )

    def start(self, paper_id):
        return self.request("POST", f"/v2/papers/{paper_id}/start")

    def restore(self, paper_id):
        return self.request("GET", f"/v2/papers/{paper_id}")

    def save(self, paper_id, question_id, value, version):
        return self.request(
            "PUT",
            f"/v2/papers/{paper_id}/drafts/{question_id}",
            {"expected_version": version, "value": value},
        )

    def submit(self, paper_id, version):
        return self.request(
            "POST",
            f"/v2/papers/{paper_id}/submit",
            {"expected_version": version, "answers": {}},
        )

    def result(self, paper_id):
        return self.request("GET", f"/v2/papers/{paper_id}/result")


def configured_transport(base_url, api_key):
    # Called only by an explicitly configured deployment; no destination is guessed.
    url = httpx.URL(base_url)
    if (
        url.scheme != "https"
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError(
            "Explicit HTTPS application URL without credentials is required"
        )
    return httpx.Client(base_url=base_url, headers={"x-api-key": api_key}, timeout=20)

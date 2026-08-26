import json
from collections.abc import Mapping
from email.message import Message
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from scripts.release_audit import OCI_ACCEPT, verify_image_digests
from scripts.verify_release_images import RegistryHttpClient, SameHostRedirectHandler

VERSION = "0.1.0"
COMMIT_SHA = "b" * 40
DIGESTS = {
    "web": "sha256:" + "1" * 64,
    "api": "sha256:" + "2" * 64,
    "worker": "sha256:" + "3" * 64,
}


def _component(url: str) -> str:
    return next(component for component in DIGESTS if f"-repo-{component}/" in url)


def _success_get(url: str, headers: Mapping[str, str]) -> tuple[int, Mapping[str, str], bytes]:
    assert url.startswith("https://ghcr.io/v2/synthetic-owner/synthetic-repo-")
    assert headers["Accept"] == OCI_ACCEPT
    assert url.endswith((f"/manifests/{VERSION}", f"/manifests/sha-{COMMIT_SHA[:12]}"))
    return 200, {"Docker-Content-Digest": DIGESTS[_component(url)]}, b'{"manifests": []}'


def _codes(http_get=_success_get) -> set[str]:  # type: ignore[no-untyped-def]
    return {
        finding.code
        for finding in verify_image_digests(
            "ghcr.io",
            "synthetic-owner/synthetic-repo",
            VERSION,
            COMMIT_SHA,
            http_get,
        )
    }


def test_digest_verifier_accepts_equal_version_and_short_sha_tags() -> None:
    assert _codes() == set()


@pytest.mark.parametrize(
    ("registry", "repository", "version", "commit_sha", "code"),
    [
        ("registry.invalid", "synthetic-owner/synthetic-repo", VERSION, COMMIT_SHA, "registry"),
        ("ghcr.io", "../unexpected", VERSION, COMMIT_SHA, "repository"),
        ("ghcr.io", "synthetic-owner/synthetic-repo", "latest", COMMIT_SHA, "version"),
        ("ghcr.io", "synthetic-owner/synthetic-repo", VERSION, "abc", "commit"),
    ],
)
def test_digest_verifier_rejects_unsafe_inputs(
    registry: str,
    repository: str,
    version: str,
    commit_sha: str,
    code: str,
) -> None:
    findings = verify_image_digests(registry, repository, version, commit_sha, _success_get)

    assert any(finding.code == f"invalid-{code}" for finding in findings)


def test_digest_verifier_rejects_non_200_without_body_disclosure() -> None:
    secret_body = b"synthetic-sensitive-response-body"

    def failing_get(url: str, headers: Mapping[str, str]) -> tuple[int, Mapping[str, str], bytes]:
        del url, headers
        return 404, {}, secret_body

    findings = verify_image_digests(
        "ghcr.io",
        "synthetic-owner/synthetic-repo",
        VERSION,
        COMMIT_SHA,
        failing_get,
    )

    assert any(finding.code == "manifest-status" for finding in findings)
    assert all(secret_body.decode() not in finding.detail for finding in findings)


@pytest.mark.parametrize("digest", [None, "sha256:ABC", "sha512:" + "a" * 64])
def test_digest_verifier_rejects_missing_or_malformed_digest(digest: str | None) -> None:
    def malformed_get(url: str, headers: Mapping[str, str]) -> tuple[int, Mapping[str, str], bytes]:
        del url, headers
        response_headers = {} if digest is None else {"Docker-Content-Digest": digest}
        return 200, response_headers, b"{}"

    codes = _codes(malformed_get)

    assert "manifest-digest" in codes


def test_digest_verifier_rejects_version_and_sha_mismatch() -> None:
    def mismatched_get(
        url: str, headers: Mapping[str, str]
    ) -> tuple[int, Mapping[str, str], bytes]:
        status, response_headers, body = _success_get(url, headers)
        if url.endswith(f"sha-{COMMIT_SHA[:12]}") and "-repo-api/" in url:
            response_headers = {"Docker-Content-Digest": "sha256:" + "4" * 64}
        return status, response_headers, body

    assert "digest-mismatch" in _codes(mismatched_get)


def test_digest_verifier_rejects_cross_image_digest_reuse() -> None:
    duplicate = "sha256:" + "5" * 64

    def duplicate_get(url: str, headers: Mapping[str, str]) -> tuple[int, Mapping[str, str], bytes]:
        del url, headers
        return 200, {"Docker-Content-Digest": duplicate}, b"{}"

    assert "cross-image-digest" in _codes(duplicate_get)


def test_redirect_handler_rejects_cross_host_redirect() -> None:
    handler = SameHostRedirectHandler()
    request = Request("https://ghcr.io/v2/synthetic/manifests/0.1.0")

    with pytest.raises(HTTPError, match="redirect host rejected"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://unexpected.invalid/manifest",
        )


class _Response:
    def __init__(self, status: int, headers: Mapping[str, str], body: bytes) -> None:
        self.status = status
        self.headers = Message()
        for name, value in headers.items():
            self.headers[name] = value
        self._body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        return self._body


class _ChallengeOpener:
    def __init__(self, *, realm: str = "https://ghcr.io/token") -> None:
        self.realm = realm
        self.requests: list[Request] = []

    def open(self, request: Request, *, timeout: float) -> _Response:
        assert timeout > 0
        self.requests.append(request)
        if len(self.requests) == 1:
            headers = Message()
            headers["WWW-Authenticate"] = (
                f'Bearer realm="{self.realm}",service="ghcr.io",'
                'scope="repository:synthetic-owner/synthetic-repo-web:pull"'
            )
            raise HTTPError(request.full_url, 401, "Unauthorized", headers, None)
        if len(self.requests) == 2:
            assert request.full_url.startswith("https://ghcr.io/token?")
            assert request.get_header("Authorization", "").startswith("Basic ")
            return _Response(200, {}, json.dumps({"token": "synthetic-bearer"}).encode())
        assert request.get_header("Authorization") == "Bearer synthetic-bearer"
        return _Response(200, {"Docker-Content-Digest": DIGESTS["web"]}, b"{}")


def test_registry_client_follows_distribution_bearer_challenge() -> None:
    client = RegistryHttpClient(actor="synthetic-actor", token="synthetic-token")
    opener = _ChallengeOpener()
    client._opener = opener  # noqa: SLF001 - deterministic transport boundary test

    status, headers, _body = client.get(
        "https://ghcr.io/v2/synthetic-owner/synthetic-repo-web/manifests/0.1.0",
        {"Accept": OCI_ACCEPT},
    )

    assert status == 200
    assert headers["Docker-Content-Digest"] == DIGESTS["web"]
    assert len(opener.requests) == 3


def test_registry_client_rejects_unapproved_token_realm() -> None:
    client = RegistryHttpClient(actor="synthetic-actor", token="synthetic-token")
    client._opener = _ChallengeOpener(  # noqa: SLF001 - deterministic transport boundary test
        realm="https://unexpected.invalid/token"
    )

    with pytest.raises(ValueError, match="authentication challenge rejected"):
        client.get(
            "https://ghcr.io/v2/synthetic-owner/synthetic-repo-web/manifests/0.1.0",
            {"Accept": OCI_ACCEPT},
        )

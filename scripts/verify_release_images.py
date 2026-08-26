#!/usr/bin/env python3
"""Verify published FamilyCare OCI tags through the registry manifest API."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
    parse_http_list,
    parse_keqv_list,
)

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.release_audit import verify_image_digests  # noqa: E402

REQUEST_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_TOKEN_BYTES = 64 * 1024


class SameHostRedirectHandler(HTTPRedirectHandler):
    """Preserve registry credentials only across same-host HTTPS redirects."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        if urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            raise HTTPError(newurl, code, "redirect host rejected", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RegistryHttpClient:
    """Small GET-only registry client with a fixed timeout and redacted failures."""

    def __init__(self, *, actor: str | None = None, token: str | None = None) -> None:
        self._actor = actor
        self._token = token
        self._opener = build_opener(SameHostRedirectHandler())

    def _open(self, request: Request, *, limit: int) -> tuple[int, Mapping[str, str], bytes]:
        with self._opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read(limit + 1)
            if len(body) > limit:
                raise ValueError("registry response is too large")
            return response.status, dict(response.headers.items()), body

    @staticmethod
    def _challenge_parameters(challenge: str) -> Mapping[str, str]:
        scheme, separator, fields = challenge.partition(" ")
        if separator != " " or scheme.casefold() != "bearer":
            raise ValueError("authentication challenge rejected")
        return parse_keqv_list(parse_http_list(fields))

    def _bearer_token(self, manifest_url: str, challenge: str) -> str:
        if not self._actor or not self._token:
            raise ValueError("registry credentials are unavailable")
        parameters = self._challenge_parameters(challenge)
        realm = parameters.get("realm", "")
        service = parameters.get("service", "")
        scope = parameters.get("scope", "")
        realm_parts = urlsplit(realm)
        manifest_parts = urlsplit(manifest_url)
        repository_match = re.fullmatch(
            r"/v2/(?P<repository>[a-z0-9][a-z0-9._/-]*)/manifests/[^/]+",
            manifest_parts.path,
        )
        if (
            realm_parts.scheme != "https"
            or realm_parts.netloc != "ghcr.io"
            or realm_parts.path != "/token"
            or realm_parts.query
            or realm_parts.fragment
            or service != "ghcr.io"
            or repository_match is None
            or scope != f"repository:{repository_match.group('repository')}:pull"
        ):
            raise ValueError("authentication challenge rejected")

        encoded = base64.b64encode(f"{self._actor}:{self._token}".encode()).decode("ascii")
        token_url = f"{realm}?{urlencode({'service': service, 'scope': scope})}"
        token_request = Request(
            token_url,
            headers={"Accept": "application/json", "Authorization": f"Basic {encoded}"},
            method="GET",
        )
        status, _headers, body = self._open(token_request, limit=MAX_TOKEN_BYTES)
        if status != 200:
            raise ValueError("registry token request failed")
        payload = json.loads(body)
        bearer = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(bearer, str) or not bearer or len(bearer) > 8192:
            raise ValueError("registry token response rejected")
        return bearer

    def get(self, url: str, headers: Mapping[str, str]) -> tuple[int, Mapping[str, str], bytes]:
        request = Request(url, headers=dict(headers), method="GET")
        try:
            return self._open(request, limit=MAX_RESPONSE_BYTES)
        except HTTPError as error:
            if error.code != 401 or not self._actor or not self._token:
                return error.code, dict(error.headers.items()), b""
            challenge = error.headers.get("WWW-Authenticate", "")
        bearer = self._bearer_token(url, challenge)
        retry_headers = {**headers, "Authorization": f"Bearer {bearer}"}
        retry = Request(url, headers=retry_headers, method="GET")
        try:
            return self._open(retry, limit=MAX_RESPONSE_BYTES)
        except HTTPError as error:
            return error.code, dict(error.headers.items()), b""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify FamilyCare GHCR image digests")
    parser.add_argument("--registry", default="ghcr.io")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Verify all three version and commit tags without printing credentials."""

    args = _parser().parse_args(argv)
    client = RegistryHttpClient(actor=os.getenv("GITHUB_ACTOR"), token=os.getenv("GHCR_TOKEN"))
    findings = verify_image_digests(
        args.registry,
        args.repository,
        args.version,
        args.commit_sha,
        client.get,
    )
    if findings:
        for finding in findings:
            print(f"{finding.code}: {finding.detail}")
        return 1
    print("release-images-ok: web api worker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

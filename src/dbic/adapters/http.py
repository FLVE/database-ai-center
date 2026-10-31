import asyncio
import json
import ssl
from typing import Any
from urllib.parse import urlsplit

import httpx

from dbic.core.models import EvidenceStatus


class BackendFailure(Exception):
    def __init__(self, status: EvidenceStatus, code: str) -> None:
        super().__init__(code)
        self.status, self.code = status, code


def validate_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname
        not in {
            "localhost",
            "127.0.0.1",
            "::1",
            "host.docker.internal",
        }
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.port is None
    ):
        raise ValueError(
            "historical endpoint requires an explicit local host port and no URL credentials"
        )
    return url.rstrip("/")


class ReadOnlyHTTP:
    def __init__(
        self,
        url: str,
        *,
        authorization: str | None = None,
        ca_file: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = validate_endpoint(url)
        self.authorization = authorization
        self.verify = ssl.create_default_context(cafile=ca_file)
        self.transport = transport

    async def json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.authorization:
            headers["Authorization"] = self.authorization
        try:
            async with asyncio.timeout(5):
                async with httpx.AsyncClient(
                    verify=self.verify,
                    timeout=4,
                    trust_env=False,
                    follow_redirects=False,
                    transport=self.transport,
                ) as client:
                    async with client.stream(
                        method, self.url + path, params=params, json=body, headers=headers
                    ) as response:
                        code = response.status_code
                        if code in {401, 403}:
                            raise BackendFailure(
                                "forbidden",
                                "authentication_failed" if code == 401 else "permission_denied",
                            )
                        if code != 200:
                            raise BackendFailure(
                                "unavailable" if code in {404, 429} or code >= 500 else "error",
                                f"http_{code}",
                            )
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 262144:
                                raise BackendFailure("error", "response_size_limit")
                        value = json.loads(data)
                        if not isinstance(value, dict):
                            raise ValueError("expected JSON object")
                        return value
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise BackendFailure("timeout", "backend_timeout") from exc
        except httpx.RequestError as exc:
            raise BackendFailure("unavailable", "connection_or_tls_failure") from exc
        except (ValueError, UnicodeError) as exc:
            raise BackendFailure("error", "invalid_backend_response") from exc

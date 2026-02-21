# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import requests

log = logging.getLogger("vw.http")


@dataclass(frozen=True)
class MTLSConfig:
    ca_cert: str
    client_cert: str
    client_key: str

    def requests_kwargs(self) -> Dict[str, Any]:
        return {
            "verify": self.ca_cert,
            "cert": (self.client_cert, self.client_key),
            "timeout": (3.0, 10.0),
        }


def _is_retryable(status_code: int) -> bool:
    return status_code in (408, 409, 425, 429, 500, 502, 503, 504)


def request_json(
    method: str,
    url: str,
    *,
    mtls: Optional[MTLSConfig] = None,
    headers: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    retries: int = 3,
    retry_delay: float = 1.0,
) -> Tuple[int, Dict[str, Any]]:
    """Small wrapper to do JSON requests with conservative retry.

    Returns (status_code, json_dict_or_empty).
    Raises only on catastrophic failures (e.g. invalid URL); on HTTP errors it returns status codes.
    """
    hdr = {"Accept": "application/json"}
    if headers:
        hdr.update(headers)

    kwargs: Dict[str, Any] = {"headers": hdr}
    if mtls:
        kwargs.update(mtls.requests_kwargs())
    if json_body is not None:
        kwargs["json"] = json_body

    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            status = resp.status_code
            if resp.content:
                try:
                    data = resp.json()
                except Exception:
                    data = {"_raw": resp.text}
            else:
                data = {}
            if status < 400:
                return status, data
            if attempt < retries and _is_retryable(status):
                time.sleep(retry_delay * (attempt + 1))
                continue
            return status, data
        except Exception as e:
            last_exc = e
            if attempt < retries:
                time.sleep(retry_delay * (attempt + 1))
                continue
            raise

    # unreachable
    raise last_exc  # type: ignore[misc]

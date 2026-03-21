"""Minimal stdlib-based Kubernetes HTTP client for agent-sandbox mode.

Used to list and patch CRD resources (SandboxClaims) when the ``kubernetes``
package is not available. Uses only the Python standard library
(``urllib.request``, ``ssl``). Handles both in-cluster and out-of-cluster
access via an explicit API URL + token.
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_IN_CLUSTER_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_IN_CLUSTER_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
_IN_CLUSTER_API_URL = "https://kubernetes.default.svc.cluster.local"


def _build_ssl_context() -> ssl.SSLContext:
    """Build an SSL context that works for both in-cluster and dev setups."""
    ctx = ssl.create_default_context()
    ca = Path(_IN_CLUSTER_CA_PATH)
    if ca.exists():
        try:
            ctx.load_verify_locations(str(ca))
        except Exception:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _read_token(override: str | None) -> str | None:
    if override:
        return override
    try:
        return Path(_IN_CLUSTER_TOKEN_PATH).read_text().strip()
    except Exception:
        return None


def _make_headers(token: str | None, content_type: str = "application/json") -> dict[str, str]:
    headers: dict[str, str] = {
        "Accept": "application/json",
        "Content-Type": content_type,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def is_k8s_api_configured(api_url: str | None, token_override: str | None) -> bool:
    """Return True if direct Kubernetes API access has been explicitly configured
    or the process is running inside a cluster.

    When neither is true (e.g. a developer running the agent-sandbox provider
    on a local machine against an externally-hosted sandbox-router) there is no
    reason to attempt K8s API calls, and failures should not produce warnings.

    Args:
        api_url: Explicit Kubernetes API base URL from config (``kube_api_url``).
        token_override: Explicit bearer token from config (``kube_token``).

    Returns:
        ``True`` if K8s API access is configured or expected to work.
    """
    if api_url or token_override:
        return True
    # Detect in-cluster by the presence of the service account token that
    # Kubernetes mounts automatically in every Pod.
    return Path(_IN_CLUSTER_TOKEN_PATH).exists()


def k8s_get(
    api_url: str | None,
    token_override: str | None,
    path: str,
    label_selector: str | None = None,
    *,
    timeout: int = 10,
) -> dict[str, Any]:
    """Perform a GET request against the Kubernetes API.

    Args:
        api_url: Base URL of the Kubernetes API server.
        token_override: Explicit bearer token (or ``None`` for in-cluster auto-read).
        path: API path (e.g. ``"/apis/extensions.agents.x-k8s.io/v1alpha1/...``).
        label_selector: Optional label selector query string.
        timeout: HTTP timeout in seconds.

    Returns:
        Parsed JSON response dict.

    Raises:
        urllib.error.HTTPError: On non-2xx responses.
        RuntimeError: On other errors.
    """
    base = (api_url or _IN_CLUSTER_API_URL).rstrip("/")
    url = f"{base}{path}"
    if label_selector:
        url += "?" + urllib.parse.urlencode({"labelSelector": label_selector})

    token = _read_token(token_override)
    headers = _make_headers(token)
    ctx = _build_ssl_context()

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        logger.debug("k8s GET %s → %s", path, exc.code)
        raise


def k8s_patch(
    api_url: str | None,
    token_override: str | None,
    path: str,
    patch: dict[str, Any],
    *,
    timeout: int = 10,
) -> dict[str, Any]:
    """Perform a strategic merge PATCH request against the Kubernetes API.

    Args:
        api_url: Base URL of the Kubernetes API server.
        token_override: Explicit bearer token.
        path: API path of the resource to patch.
        patch: Merge-patch body dict.
        timeout: HTTP timeout in seconds.

    Returns:
        Parsed JSON response dict of the patched resource.

    Raises:
        urllib.error.HTTPError: On non-2xx responses.
    """
    base = (api_url or _IN_CLUSTER_API_URL).rstrip("/")
    url = f"{base}{path}"

    token = _read_token(token_override)
    headers = _make_headers(token, content_type="application/merge-patch+json")
    ctx = _build_ssl_context()

    body = json.dumps(patch).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        logger.debug("k8s PATCH %s → %s", path, exc.code)
        raise

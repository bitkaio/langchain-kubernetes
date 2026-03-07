"""WebSocket exec wrapper and tar-based file transfer for Kubernetes Pods."""

from __future__ import annotations

import io
import json
import logging
import tarfile
from typing import TYPE_CHECKING

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from kubernetes.stream import stream
from kubernetes.stream.ws_client import ERROR_CHANNEL

if TYPE_CHECKING:
    import kubernetes.client

logger = logging.getLogger(__name__)

# Size of chunks streamed from the WebSocket exec response
_CHUNK_SIZE = 4096


def exec_command(
    core_v1: "kubernetes.client.CoreV1Api",
    *,
    pod_name: str,
    namespace: str,
    container: str,
    command: str,
    timeout: float | None = None,
) -> ExecuteResponse:
    """Run *command* inside a Pod container via the Kubernetes exec API.

    Uses the ``kubernetes`` Python client's ``stream`` helper (WebSocket).
    stdout and stderr are captured and combined in the returned
    :class:`~deepagents.backends.protocol.ExecuteResponse`.

    Args:
        core_v1: Authenticated ``CoreV1Api`` instance.
        pod_name: Name of the target Pod.
        namespace: Namespace of the target Pod.
        container: Container name within the Pod.
        command: Shell command string to execute via ``/bin/sh -c``.
        timeout: Seconds before the exec call is forcibly closed.  ``None``
            means wait indefinitely (passed as ``0`` to the client).

    Returns:
        :class:`~deepagents.backends.protocol.ExecuteResponse` with combined
        output, exit code, and ``truncated=False``.
    """
    ws_timeout = timeout if timeout is not None else 0

    resp = stream(
        core_v1.connect_get_namespaced_pod_exec,
        pod_name,
        namespace,
        container=container,
        command=["/bin/sh", "-c", command],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
        _preload_content=False,
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    try:
        resp.run_forever(timeout=ws_timeout)

        # After run_forever the WebSocket is closed; read buffered channel data
        if resp.peek_stdout():
            stdout_chunks.append(resp.read_stdout())
        if resp.peek_stderr():
            stderr_chunks.append(resp.read_stderr())
    finally:
        resp.close()

    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)

    output = stdout
    if stderr:
        output = output + "\n" + stderr if output else stderr

    # Parse return code from the error channel status message
    exit_code: int = 0
    try:
        err_msg = resp.read_channel(ERROR_CHANNEL)
        if err_msg:
            status = json.loads(err_msg)
            if status.get("status") == "Success":
                exit_code = 0
            else:
                details = status.get("details", {})
                causes = details.get("causes", [])
                for cause in causes:
                    if cause.get("reason") == "ExitCode":
                        exit_code = int(cause.get("message", "1"))
                        break
                else:
                    exit_code = 1
    except Exception:
        # Fall back: non-zero if stderr is non-empty
        exit_code = 1 if stderr.strip() else 0

    return ExecuteResponse(output=output, exit_code=exit_code, truncated=False)


def upload_files_tar(
    core_v1: "kubernetes.client.CoreV1Api",
    *,
    pod_name: str,
    namespace: str,
    container: str,
    files: list[tuple[str, bytes]],
) -> list[FileUploadResponse]:
    """Upload files into a Pod by piping a tar archive via stdin exec.

    Builds an in-memory ``tar`` archive from *files* and streams it to the
    Pod through ``tar xf - -C /``.

    Args:
        core_v1: Authenticated ``CoreV1Api`` instance.
        pod_name: Target Pod name.
        namespace: Target Pod namespace.
        container: Container name within the Pod.
        files: List of ``(absolute_path, content_bytes)`` tuples.

    Returns:
        List of :class:`~deepagents.backends.protocol.FileUploadResponse`
        objects, one per input file, in the same order.
    """
    # Build in-memory tar archive
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path, content in files:
            info = tarfile.TarInfo(name=path.lstrip("/"))
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    tar_bytes = buf.getvalue()

    responses: list[FileUploadResponse] = []
    error: str | None = None

    try:
        resp = stream(
            core_v1.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            container=container,
            command=["tar", "xf", "-", "-C", "/"],
            stderr=True,
            stdin=True,
            stdout=True,
            tty=False,
            _preload_content=False,
        )

        # Send tar data in chunks then close stdin
        offset = 0
        while offset < len(tar_bytes):
            chunk = tar_bytes[offset : offset + _CHUNK_SIZE]
            resp.write_stdin(chunk)
            offset += len(chunk)

        resp.write_stdin(b"")  # signal EOF
        resp.run_forever(timeout=30)

        stderr = resp.read_stderr() if resp.peek_stderr() else ""
        if stderr.strip():
            logger.warning("tar upload stderr: %s", stderr.strip())
            error = stderr.strip()
        resp.close()
    except Exception as exc:
        logger.error("upload_files_tar failed: %s", exc)
        error = str(exc)

    for path, _ in files:
        responses.append(FileUploadResponse(path=path, error=error))  # type: ignore[arg-type]

    return responses


def download_files_tar(
    core_v1: "kubernetes.client.CoreV1Api",
    *,
    pod_name: str,
    namespace: str,
    container: str,
    paths: list[str],
) -> list[FileDownloadResponse]:
    """Download files from a Pod by streaming a tar archive from stdout.

    Runs ``tar cf - <paths>`` inside the Pod and parses the tar archive from
    stdout.

    Args:
        core_v1: Authenticated ``CoreV1Api`` instance.
        pod_name: Target Pod name.
        namespace: Target Pod namespace.
        container: Container name within the Pod.
        paths: Absolute paths to download from the Pod.

    Returns:
        List of :class:`~deepagents.backends.protocol.FileDownloadResponse`
        objects.  Files that could not be read have ``content=None`` and a
        non-None ``error``.
    """
    if not paths:
        return []

    command = ["tar", "cf", "-"] + list(paths)

    stdout_buf = io.BytesIO()

    try:
        resp = stream(
            core_v1.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            container=container,
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        resp.run_forever(timeout=60)

        raw = resp.read_stdout()
        if raw:
            stdout_buf.write(raw.encode("latin-1") if isinstance(raw, str) else raw)

        stderr = resp.read_stderr() if resp.peek_stderr() else ""
        resp.close()
    except Exception as exc:
        logger.error("download_files_tar failed: %s", exc)
        return [
            FileDownloadResponse(path=p, content=None, error="file_not_found")
            for p in paths
        ]

    # Parse tar archive
    stdout_buf.seek(0)
    result_map: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=stdout_buf, mode="r") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    f = tar.extractfile(member)
                    if f:
                        # member.name strips the leading slash
                        key = "/" + member.name.lstrip("/")
                        result_map[key] = f.read()
    except Exception as exc:
        logger.warning("Failed to parse tar archive: %s", exc)

    responses: list[FileDownloadResponse] = []
    for path in paths:
        content = result_map.get(path)
        if content is None:
            responses.append(
                FileDownloadResponse(path=path, content=None, error="file_not_found")
            )
        else:
            responses.append(FileDownloadResponse(path=path, content=content, error=None))
    return responses


# ---------------------------------------------------------------------------
# Async variants (requires kubernetes_asyncio)
# ---------------------------------------------------------------------------


async def async_exec_command(
    core_v1: object,
    *,
    pod_name: str,
    namespace: str,
    container: str,
    command: str,
    timeout: float | None = None,
) -> ExecuteResponse:
    """Async version of :func:`exec_command` using ``kubernetes_asyncio``.

    Args:
        core_v1: ``kubernetes_asyncio.client.CoreV1Api`` instance.
        pod_name: Target Pod name.
        namespace: Target Pod namespace.
        container: Container name within the Pod.
        command: Shell command string.
        timeout: Maximum seconds to wait.

    Returns:
        :class:`~deepagents.backends.protocol.ExecuteResponse`.
    """
    try:
        from kubernetes_asyncio.stream import stream as async_stream
    except ImportError as exc:
        raise ImportError(
            "kubernetes_asyncio is required for async exec. "
            "Install it with: pip install 'langchain-kubernetes[async]'"
        ) from exc

    import asyncio

    ws_timeout = timeout if timeout is not None else None

    resp = await async_stream(
        core_v1.connect_get_namespaced_pod_exec,  # type: ignore[attr-defined]
        pod_name,
        namespace,
        container=container,
        command=["/bin/sh", "-c", command],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    async def _read():
        async for msg in resp:
            channel = msg.get("channel")
            data = msg.get("data", "")
            if channel == 1:
                stdout_parts.append(data)
            elif channel == 2:
                stderr_parts.append(data)

    if ws_timeout is not None:
        try:
            await asyncio.wait_for(_read(), timeout=ws_timeout)
        except asyncio.TimeoutError:
            stdout = "".join(stdout_parts)
            stderr = "".join(stderr_parts)
            output = stdout + ("\n" + stderr if stderr and stdout else stderr)
            return ExecuteResponse(
                output=output or "Command timed out", exit_code=-1, truncated=True
            )
    else:
        await _read()

    stdout = "".join(stdout_parts)
    stderr = "".join(stderr_parts)
    output = stdout
    if stderr:
        output = output + "\n" + stderr if output else stderr

    exit_code = 0 if not stderr.strip() else 1

    return ExecuteResponse(output=output, exit_code=exit_code, truncated=False)

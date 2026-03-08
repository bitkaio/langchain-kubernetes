"""Minimal helpers: logging and response mapping."""

from __future__ import annotations

import logging

from deepagents.backends.protocol import ExecuteResponse


def map_execution_result(result: object, *, default_timeout: int = 60) -> ExecuteResponse:
    """Map a ``SandboxClient`` ``ExecutionResult`` to ``ExecuteResponse``.

    Args:
        result: ``ExecutionResult`` returned by ``SandboxClient.run()``.
        default_timeout: Unused — kept for call-site compatibility.

    Returns:
        :class:`~deepagents.backends.protocol.ExecuteResponse` with combined
        stdout/stderr output, the process exit code, and ``truncated=False``.
    """
    stdout: str = getattr(result, "stdout", "") or ""
    stderr: str = getattr(result, "stderr", "") or ""
    exit_code: int = getattr(result, "exit_code", -1)

    if stdout and stderr:
        output = stdout + stderr
    else:
        output = stdout or stderr

    return ExecuteResponse(output=output, exit_code=exit_code, truncated=False)


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger.

    Args:
        name: Usually ``__name__`` of the calling module.

    Returns:
        Configured :class:`logging.Logger`.
    """
    return logging.getLogger(name)

# langchain-kubernetes Project Memory

## Project
`langchain-kubernetes` — Kubernetes sandbox provider for DeepAgents (LangChain).

## Key Decisions

### SandboxProvider base class
`deepagents_cli` is NOT published to PyPI. Do NOT import from it.
Define `SandboxProvider`, `SandboxError`, `SandboxNotFoundError` locally in
`langchain_kubernetes/_provider_base.py`.

### Module-level imports for mockability
Always use `from kubernetes.stream import stream` at **module level** in
`exec_transport.py`. Never re-import `stream` inside function bodies — inner imports
shadow the module-level name and bypass `@patch("langchain_kubernetes.exec_transport.stream")`.

### Venv
Use `uv venv .venv` + `uv pip install -e ".[dev]"` (system pip can't install
hatchling editable on Python 3.14 without --break-system-packages, but even then
fails). Create README.md before installing or hatchling errors.

## Package Structure
- `_utils.py` — labels, ID generation, poll_until
- `config.py` — KubernetesProviderConfig dataclass
- `manifests.py` — Pod/Namespace/NetworkPolicy manifest builders (pure dicts)
- `exec_transport.py` — module-level `stream` import, exec_command/upload/download
- `sandbox.py` — KubernetesSandbox(BaseSandbox)
- `provider.py` — KubernetesProvider(SandboxProvider)
- `_provider_base.py` — local SandboxProvider/SandboxError/SandboxNotFoundError

## Test Status
- 45/45 unit tests pass (`uv run pytest tests/unit/`)
- Integration tests require kind cluster (`pytest -m integration`)

## DeepAgents API (from source)
- `BaseSandbox` abstract methods: `execute()`, `id`, `upload_files()`, `download_files()`
- `ExecuteResponse(output, exit_code, truncated)`
- `FileUploadResponse(path, error)`; `FileDownloadResponse(path, content, error)`
- `FileOperationError`: Literal["file_not_found","permission_denied","is_directory","invalid_path"]

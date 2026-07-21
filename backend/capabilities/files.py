from __future__ import annotations

import os
import tempfile
from pathlib import Path, PurePosixPath

from backend.capabilities.policy import CapabilityAuthorizer, CapabilityDenied


class AppFileError(RuntimeError):
    pass


class AppFileGateway:
    def __init__(self, app_manager):
        self.app_manager = app_manager
        self.authorizer = CapabilityAuthorizer(manifest_loader=app_manager.get_manifest)

    @staticmethod
    def _validate_relative_path(path: str, *, allow_directory: bool = False) -> PurePosixPath:
        if not isinstance(path, str) or not path or "\x00" in path or "\\" in path or path.startswith("/"):
            raise AppFileError("File path must be a non-empty normalized relative path")
        pure = PurePosixPath(path)
        if any(part in {"", ".", ".."} for part in pure.parts):
            raise AppFileError("File path escape is not allowed")
        if not allow_directory and path.endswith("/"):
            raise AppFileError("File path must identify a regular file")
        return pure

    def _data_root(self, app_id: str) -> Path:
        try:
            app_dir = self.app_manager.app_path(app_id)
        except ValueError as exc:
            raise AppFileError(str(exc)) from exc
        root = app_dir / "data"
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise AppFileError("App data root must not be a symbolic link")
        return root

    @staticmethod
    def _reject_links(root: Path, relative: PurePosixPath) -> Path:
        cursor = root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                raise AppFileError("Symbolic links are not allowed in App data paths")
        resolved = (root / Path(*relative.parts)).resolve(strict=False)
        root_resolved = root.resolve(strict=True)
        if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
            raise AppFileError("File path escape is not allowed")
        return resolved

    def _authorize(
        self,
        app_id: str,
        operation: str,
        path: str,
        *,
        size: int | None = None,
        revision: str | None = None,
        grants_digest: str | None = None,
    ) -> None:
        try:
            self.authorizer.authorize_file(
                app_id,
                operation,
                path,
                size=size,
                manifest_revision=revision,
                grants_digest=grants_digest,
            )
        except CapabilityDenied as exc:
            maximum = exc.details.get("max_bytes")
            suffix = f" (maximum {maximum} bytes)" if maximum is not None else ""
            raise AppFileError(f"File capability denied: {exc}{suffix}") from exc

    def write_text(
        self,
        app_id: str,
        path: str,
        text: str,
        *,
        manifest_revision: str | None = None,
        grants_digest: str | None = None,
    ) -> None:
        if not isinstance(text, str):
            raise AppFileError("File content must be text")
        raw = text.encode("utf-8")
        relative = self._validate_relative_path(path)
        self._authorize(
            app_id,
            "write",
            path,
            size=len(raw),
            revision=manifest_revision,
            grants_digest=grants_digest,
        )
        root = self._data_root(app_id)
        target = self._reject_links(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._reject_links(root, relative)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def read_text(
        self,
        app_id: str,
        path: str,
        *,
        manifest_revision: str | None = None,
        grants_digest: str | None = None,
    ) -> str:
        relative = self._validate_relative_path(path)
        self._authorize(app_id, "read", path, revision=manifest_revision, grants_digest=grants_digest)
        target = self._reject_links(self._data_root(app_id), relative)
        if not target.is_file():
            raise AppFileError("App data file not found")
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise AppFileError("App data file is not readable UTF-8 text") from exc

    def list_files(
        self,
        app_id: str,
        path: str,
        *,
        manifest_revision: str | None = None,
        grants_digest: str | None = None,
    ) -> list[str]:
        relative = self._validate_relative_path(path, allow_directory=True)
        # Directory listing is authorized by requiring that possible children match the read prefix.
        probe = f"{path.rstrip('/')}/__list__"
        self._authorize(app_id, "list", probe, revision=manifest_revision, grants_digest=grants_digest)
        root = self._data_root(app_id)
        directory = self._reject_links(root, relative)
        if not directory.exists():
            return []
        if not directory.is_dir():
            raise AppFileError("App data list path is not a directory")
        result: list[str] = []
        for candidate in directory.rglob("*"):
            if candidate.is_symlink():
                raise AppFileError("Symbolic links are not allowed in App data paths")
            if candidate.is_file():
                result.append(candidate.relative_to(root).as_posix())
        return sorted(result)

    def delete(
        self,
        app_id: str,
        path: str,
        *,
        manifest_revision: str | None = None,
        grants_digest: str | None = None,
    ) -> None:
        relative = self._validate_relative_path(path)
        self._authorize(app_id, "delete", path, revision=manifest_revision, grants_digest=grants_digest)
        target = self._reject_links(self._data_root(app_id), relative)
        if not target.is_file():
            raise AppFileError("App data file not found")
        target.unlink()

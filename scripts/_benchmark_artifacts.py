"""Durable benchmark artifact and provenance helpers.

Benchmark checkpoints can represent expensive model work.  This module keeps their recovery
contract small and shared: bounded JSON parsing, fsynced run receipts, deterministic dirty-tree
fingerprints, and no-clobber publication primitives.  It never evaluates checkpoint content.
"""
from __future__ import annotations

import hashlib
import functools
import importlib.metadata
import json
import os
import platform
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Iterable

_MAX_RECEIPT_BYTES = 256 * 1024
_MAX_CHECKPOINT_BYTES = 128 * 1024 * 1024
_MAX_CHECKPOINT_LINE_BYTES = 8 * 1024 * 1024
_MAX_CHECKPOINT_ROWS = 1_000_000
_MAX_INSTALLED_FILES = 10_000
_MAX_INSTALLED_BYTES = 512 * 1024 * 1024
_INSTALLED_PAYLOAD_ROOTS = frozenset({"agent", "scripts", "profiles", "formal"})

# Untracked generated benchmark records must not make a code revision change merely because a run
# checkpoint was created.  Untracked executable/configuration inputs under these roots do matter.
_RELEVANT_ROOT_FILES = frozenset({
    "AGENTS.md", "CLAUDE.md", "Makefile", "pyproject.toml", "uv.lock",
})


def _json_object_no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON object key {key!r}")
        obj[key] = value
    return obj


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def object_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def indices_sha256(indices: Iterable[str]) -> str:
    return object_sha256([str(idx) for idx in indices])


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def installed_distribution_revision(distribution: str = "mathagent", *,
                                    expected_root: Path | None = None) -> str:
    """Hash the *current* executable/configuration payload of a non-editable wheel install.

    ``RECORD`` is used only as the distribution-owned file inventory.  Its declared hashes are not a
    trustworthy receipt for bytes currently on disk: a post-install edit leaves ``RECORD`` unchanged.
    Re-hashing every relevant current file both distinguishes rebuilt wheels and detects tampering.
    """
    try:
        dist = importlib.metadata.distribution(distribution)
        version = dist.version
        files = dist.files
        if not files:
            raise ValueError("distribution has no installed-file inventory")
        base = Path(dist.locate_file("")).resolve(strict=True)
        if expected_root is not None and base != expected_root.resolve(strict=True):
            raise ValueError("installed distribution does not match the requested code root")
        selected: list[tuple[str, object]] = []
        for entry in files:
            rel_text = entry.as_posix()
            rel = PurePosixPath(rel_text)
            if rel.is_absolute() or not rel.parts or any(part in {"", ".", ".."} for part in rel.parts):
                raise ValueError(f"unsafe installed path: {rel_text}")
            if rel.parts[0] not in _INSTALLED_PAYLOAD_ROOTS:
                continue
            if "__pycache__" in rel.parts or rel.suffix.lower() in {".pyc", ".pyo"}:
                continue
            selected.append((rel_text, entry))
        selected.sort(key=lambda item: item[0])
        if not selected or len(selected) > _MAX_INSTALLED_FILES:
            raise ValueError("installed payload file count is outside the supported bound")

        # RECORD is the ownership inventory, but it is not allowed to hide added executable/config
        # files. Walk each owned top-level package and require the complete current source payload to
        # match the declared set (runtime bytecode caches are the sole explicit exception).
        declared = {rel_text for rel_text, _entry in selected}
        current: set[str] = set()
        for root_name in sorted({PurePosixPath(rel).parts[0] for rel in declared}):
            payload_root = base / root_name
            root_info = payload_root.lstat()
            if not stat.S_ISDIR(root_info.st_mode) or payload_root.is_symlink():
                raise ValueError(f"installed payload root is not a regular directory: {root_name}")
            for directory, dirnames, filenames in os.walk(payload_root, followlinks=False):
                dirnames[:] = sorted(name for name in dirnames if name != "__pycache__")
                for dirname in dirnames:
                    child = Path(directory) / dirname
                    if child.is_symlink():
                        raise ValueError(f"installed payload contains a directory symlink: {child}")
                for filename in sorted(filenames):
                    path = Path(directory) / filename
                    rel = path.relative_to(base)
                    if "__pycache__" in rel.parts or path.suffix.lower() in {".pyc", ".pyo"}:
                        continue
                    info = path.lstat()
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError(f"installed payload contains a non-regular file: {rel}")
                    current.add(rel.as_posix())
                    if len(current) > _MAX_INSTALLED_FILES:
                        raise ValueError("installed payload file count exceeds the supported bound")
        if current != declared:
            raise ValueError("current installed payload does not match its distribution inventory")

        digest = hashlib.sha256(b"mathagent-installed-payload-v1\0")
        total_bytes = 0
        for rel_text, entry in selected:
            path = Path(dist.locate_file(entry))
            resolved = path.resolve(strict=True)
            if resolved != base and base not in resolved.parents:
                raise ValueError(f"installed path escapes distribution root: {rel_text}")
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"installed payload is not a regular file: {rel_text}")
            total_bytes += info.st_size
            if total_bytes > _MAX_INSTALLED_BYTES:
                raise ValueError("installed payload exceeds the supported byte bound")
            rel_bytes = rel_text.encode("utf-8")
            digest.update(len(rel_bytes).to_bytes(8, "big"))
            digest.update(rel_bytes)
            digest.update(info.st_size.to_bytes(8, "big"))
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
        return f"dist:{distribution}@{version}+payload.sha256:{digest.hexdigest()}"
    except Exception:
        return "unknown+unverified"


def _has_git_marker(root: Path) -> bool:
    """Whether a conventional worktree marker exists at or above ``root``."""
    return any((candidate / ".git").exists() or (candidate / ".git").is_symlink()
               for candidate in (root, *root.parents))


def _relevant_untracked(path: str) -> bool:
    normalized = path.replace("\\", "/")
    suffix = Path(normalized).suffix.lower()
    if normalized in _RELEVANT_ROOT_FILES:
        return True
    if normalized.startswith("agent/"):
        return suffix in {".md", ".py", ".yaml", ".yml", ".lean"} \
            or (normalized.startswith("agent/gates/") and suffix == ".json")
    if normalized.startswith("scripts/"):
        return suffix in {".py", ".toml", ".yaml", ".yml"}
    if normalized.startswith("formal/"):
        return suffix in {".json", ".lean", ".toml"} or Path(normalized).name == "lean-toolchain"
    if normalized.startswith("profiles/"):
        return suffix in {".py", ".yaml", ".yml"}
    return False


def code_revision(repo_root: Path) -> str:
    """Return HEAD, or ``HEAD+dirty.<sha256>`` over all tracked changes and relevant untracked files.

    Unlike a bare ``+dirty`` marker, the digest distinguishes two dirty source trees.  Generated run
    records are excluded, while untracked source/configuration inputs that can affect execution are
    included with both their repository-relative path and content.  Any Git/read failure is explicit.
    """
    root = repo_root.resolve()
    try:
        discovery = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True, text=True,
            timeout=10, check=False,
        )
    except subprocess.TimeoutExpired:
        # A transient Git failure is not evidence that the path is a wheel install.
        return "unknown+unverified"
    except OSError:
        # A missing Git binary outside any conventional worktree still permits a wheel receipt; an
        # inaccessible checkout must never be relabeled with unrelated distribution metadata.
        return (installed_distribution_revision(expected_root=root) if not _has_git_marker(root)
                else "unknown+unverified")
    if discovery.returncode != 0:
        return ("unknown+unverified" if _has_git_marker(root)
                else installed_distribution_revision(expected_root=root))
    try:
        top = Path(discovery.stdout.strip()).resolve(strict=True)
    except (OSError, ValueError):
        return "unknown+unverified"
    if top != root:
        # This is typically an installed venv nested inside an outer source checkout. Its imported
        # bytes are not identified by the outer checkout's HEAD.
        return installed_distribution_revision(expected_root=root)

    # Git identity is now established for exactly the requested worktree. Any subsequent read/diff
    # failure is unverified; falling back to installed metadata here would create false provenance.
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
            timeout=10, check=True,
        ).stdout.strip()
        if not head:
            raise ValueError("empty git HEAD")
        digest = hashlib.sha256(b"mathagent-dirty-tree-v2\0")
        tracked_bytes = 0
        tracked_digest = hashlib.sha256()
        # Send arbitrarily large binary diffs to an anonymous temporary file, then hash in chunks. This
        # preserves the exact deterministic diff fingerprint without materializing it in Python memory.
        with tempfile.TemporaryFile() as tracked_fh:
            subprocess.run(
                ["git", "diff", "--binary", "--full-index", "--no-ext-diff", "HEAD", "--"],
                cwd=root, stdout=tracked_fh, stderr=subprocess.DEVNULL, timeout=30, check=True,
            )
            tracked_fh.seek(0)
            for chunk in iter(lambda: tracked_fh.read(1024 * 1024), b""):
                tracked_bytes += len(chunk)
                tracked_digest.update(chunk)
        digest.update(b"tracked\0")
        digest.update(tracked_bytes.to_bytes(8, "big"))
        digest.update(tracked_digest.digest())

        relevant_untracked = 0
        with tempfile.TemporaryFile() as untracked_fh:
            subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard", "-z"],
                cwd=root, stdout=untracked_fh, stderr=subprocess.DEVNULL, timeout=10, check=True,
            )
            untracked_fh.seek(0)
            carry = b""
            while True:
                chunk = untracked_fh.read(1024 * 1024)
                if not chunk:
                    pieces = [carry] if carry else []
                else:
                    pieces = (carry + chunk).split(b"\0")
                    carry = pieces.pop()
                for raw_path in pieces:
                    if not raw_path:
                        continue
                    rel = raw_path.decode("utf-8", errors="strict")
                    if not _relevant_untracked(rel):
                        continue
                    relevant_untracked += 1
                    path = root / Path(rel)
                    resolved = path.resolve(strict=True)
                    if root != resolved and root not in resolved.parents:
                        raise ValueError(f"untracked path escapes repository: {rel}")
                    digest.update(b"untracked\0")
                    digest.update(len(raw_path).to_bytes(8, "big"))
                    digest.update(raw_path)
                    if path.is_symlink():
                        target = os.readlink(path).encode("utf-8")
                        digest.update(b"symlink\0")
                        digest.update(len(target).to_bytes(8, "big"))
                        digest.update(hashlib.sha256(target).digest())
                    elif path.is_file():
                        digest.update(b"file\0")
                        before = path.stat()
                        content_digest = hashlib.sha256()
                        with path.open("rb") as fh:
                            for data in iter(lambda: fh.read(1024 * 1024), b""):
                                content_digest.update(data)
                        after = path.stat()
                        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                            raise ValueError(f"untracked source/config changed while hashing: {rel}")
                        digest.update(after.st_size.to_bytes(8, "big"))
                        digest.update(content_digest.digest())
                    else:
                        raise ValueError(f"untracked source/config path is not a file: {rel}")
                if not chunk:
                    break
        if tracked_bytes == 0 and relevant_untracked == 0:
            return head
        return f"{head}+dirty.{digest.hexdigest()}"
    except Exception:
        return "unknown+unverified"


@functools.lru_cache(maxsize=8)
def cached_code_revision(repo_root: Path) -> str:
    """One process-start snapshot, shared by runners imported in the same Python process."""
    return code_revision(repo_root)


def runtime_versions() -> dict[str, object]:
    """Versions needed to interpret or reproduce benchmark grading and orchestration."""
    packages: dict[str, str | None] = {}
    for distribution in ("mathagent", "sympy", "jsonschema", "pydantic", "PyYAML", "datasets"):
        try:
            packages[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            packages[distribution] = None
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "packages": packages,
    }


def fsync_directory(directory: Path) -> None:
    """Best-effort directory fsync (unsupported by some Windows/filesystem combinations)."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(directory, flags)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        os.close(fd)


def receipt_path(checkpoint: Path) -> Path:
    return checkpoint.with_name(f"{checkpoint.name}.receipt.json")


def write_receipt(path: Path, receipt: dict) -> str:
    payload = canonical_json_bytes(receipt)
    if len(payload) > _MAX_RECEIPT_BYTES:
        raise ValueError("benchmark receipt is too large")
    with path.open("xb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    fsync_directory(path.parent)
    return hashlib.sha256(payload).hexdigest()


def load_receipt(path: Path) -> tuple[dict, str]:
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"receipt must be a regular non-symlink file: {path}")
    if st.st_size <= 0 or st.st_size > _MAX_RECEIPT_BYTES:
        raise ValueError(f"receipt size is outside 1..{_MAX_RECEIPT_BYTES} bytes")
    payload = path.read_bytes()
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_json_object_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid benchmark receipt: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("benchmark receipt must be a JSON object")
    return value, hashlib.sha256(payload).hexdigest()


def load_checkpoint_rows(checkpoint: Path, *, max_rows: int = _MAX_CHECKPOINT_ROWS
                         ) -> tuple[list[dict], int]:
    """Load only newline-committed rows and return ``(rows, durable_byte_count)``.

    A final unterminated fragment was never a completed fsynced row under the writers' contract.  It is
    ignored here and may be truncated *after* every preceding row and the run receipt validate.
    """
    st = checkpoint.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError(f"checkpoint must be a regular non-symlink file: {checkpoint}")
    if st.st_size > _MAX_CHECKPOINT_BYTES:
        raise ValueError(f"checkpoint exceeds {_MAX_CHECKPOINT_BYTES} bytes")
    rows: list[dict] = []
    durable_size = 0
    with checkpoint.open("rb") as fh:
        while True:
            raw = fh.readline(_MAX_CHECKPOINT_LINE_BYTES + 1)
            if not raw:
                break
            if len(raw) > _MAX_CHECKPOINT_LINE_BYTES:
                raise ValueError(f"checkpoint line exceeds {_MAX_CHECKPOINT_LINE_BYTES} bytes")
            if not raw.endswith(b"\n"):
                break
            durable_size += len(raw)
            if not raw.strip():
                raise ValueError("checkpoint contains a blank row")
            try:
                value = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_object_no_duplicates)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"invalid checkpoint row {len(rows) + 1}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"checkpoint row {len(rows) + 1} must be a JSON object")
            rows.append(value)
            if len(rows) > max_rows:
                raise ValueError(f"checkpoint exceeds the selected-row bound ({max_rows})")
    return rows, durable_size


def truncate_checkpoint(checkpoint: Path, durable_size: int) -> None:
    current = checkpoint.stat().st_size
    if durable_size < 0 or durable_size > current:
        raise ValueError("invalid durable checkpoint offset")
    if durable_size == current:
        return
    with checkpoint.open("r+b") as fh:
        fh.truncate(durable_size)
        fh.flush()
        os.fsync(fh.fileno())


def acquire_checkpoint_lock(checkpoint: Path):
    """Acquire a non-blocking OS lock for a resume attempt; closing the returned handle releases it."""
    fh = checkpoint.open("rb")
    try:
        if os.name == "posix":
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif os.name == "nt":  # pragma: no cover - CI and project execution use WSL
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # fail closed rather than permit two paid resumptions to corrupt one checkpoint
            raise OSError(f"checkpoint locking is unsupported on {os.name}")
    except Exception:
        fh.close()
        raise
    return fh


def prepare_text_checkpoint(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    fsync_directory(path.parent)


def prepare_data_checkpoint(source: Path, prepared: Path) -> None:
    """Create a fsynced byte snapshot for publication while retaining the resumable source."""
    if prepared.exists() or prepared.is_symlink():
        st = prepared.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise ValueError(f"prepared checkpoint must be a regular non-symlink file: {prepared}")
        prepared.unlink()
    with source.open("rb") as src, prepared.open("xb") as dst:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    fsync_directory(prepared.parent)


def _unlink_if_same(link: Path, source: Path) -> None:
    try:
        if os.path.samefile(link, source):
            link.unlink()
    except (FileNotFoundError, OSError):
        pass


def publish_pair(*, summary_checkpoint: Path, summary_final: Path,
                 data_checkpoint: Path, data_final: Path) -> None:
    """Publish a prepared pair without ever committing data before its summary.

    ``data_final`` is the commit point.  If that link fails after the summary link succeeds, only the
    link proven to reference our prepared summary is rolled back; both checkpoints remain recoverable.
    """
    summary_linked = False
    data_linked = False
    try:
        os.link(summary_checkpoint, summary_final)
        summary_linked = True
        fsync_directory(summary_final.parent)
        os.link(data_checkpoint, data_final)
        data_linked = True
        fsync_directory(data_final.parent)
    except BaseException:
        if summary_linked and not data_linked:
            _unlink_if_same(summary_final, summary_checkpoint)
            fsync_directory(summary_final.parent)
        raise


def finish_publication(checkpoints: Iterable[Path]) -> None:
    """Remove recovery files after their final artifact(s) have committed."""
    parents: set[Path] = set()
    for path in checkpoints:
        parents.add(path.parent)
        path.unlink(missing_ok=True)
    for parent in parents:
        fsync_directory(parent)


def publish_single(checkpoint: Path, final: Path, *, overwrite: bool = False) -> None:
    if overwrite:
        os.replace(checkpoint, final)
    else:
        os.link(checkpoint, final)
        checkpoint.unlink()
    fsync_directory(final.parent)

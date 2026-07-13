"""Bounded, process-tree-contained transport for local model CLIs.

The launcher is not a security boundary, but it must not leak descendants or turn a verbose model
into a disk-exhaustion primitive.  On POSIX every invocation gets its own process group; on Windows
the root starts suspended and enters a kill-on-close Job Object before it can spawn children.
"""
from __future__ import annotations

import math
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import BinaryIO, Mapping, Type

from agent.gates.windows_job import WindowsMemoryJob, resume_suspended_windows_process


_READ_CHUNK_BYTES = 64 * 1024
_POLL_INTERVAL_S = 0.01


def prepare_cli_launcher(
    launcher: str,
    *,
    error_type: Type[RuntimeError],
    label: str,
) -> tuple[list[str], str | None]:
    """Return an argv prefix and optional temp root for a native or Windows batch launcher.

    A Windows ``.cmd`` discovered from WSL cannot run from Linux ``/tmp``: ``cmd.exe`` receives a
    ``\\\\wsl.localhost`` cwd, which it rejects.  Translate the mounted launcher to a drive path and
    place the throwaway cwd on the same Windows-visible mount.  Other launchers retain the platform
    default temporary directory.
    """
    if not launcher.lower().endswith((".cmd", ".bat")):
        return [launcher], None
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", launcher], None

    path = Path(launcher).resolve(strict=False)
    parts = path.parts
    if len(parts) < 4 or parts[0] != "/" or parts[1] != "mnt" or len(parts[2]) != 1:
        raise error_type(
            f"{label} Windows batch launcher must be on a /mnt/<drive> path under WSL"
        )
    drive = parts[2]
    windows_launcher = f"{drive.upper()}:\\" + "\\".join(parts[3:])
    mount = Path("/mnt") / drive
    candidates: list[Path] = []
    if len(parts) >= 5 and parts[3].lower() == "users":
        candidates.append(mount / "Users" / parts[4] / "AppData" / "Local" / "Temp")
    candidates.append(mount / "Windows" / "Temp")
    temp_root = next((candidate for candidate in candidates if candidate.is_dir()), None)
    if temp_root is None:
        raise error_type(f"{label} could not find a Windows-visible temporary directory")
    return [os.environ.get("COMSPEC", "cmd.exe"), "/c", windows_launcher], str(temp_root)


def _terminate_tree(proc: subprocess.Popen, job: WindowsMemoryJob | None) -> None:
    """Terminate the containment unit even when its original root has already exited."""
    job_failed = False
    if os.name == "nt" and job is not None:
        try:
            job.close()  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE reaches surviving descendants.
        except Exception as exc:
            # If TerminateJobObject succeeded but CloseHandle failed, descendants are already dead.
            # Do not taskkill a reaped root PID that Windows may have reassigned meanwhile.
            job_failed = not bool(getattr(exc, "tree_terminated", False))
    if os.name != "nt":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    elif job is None or job_failed:
        # Assignment can fail before a Job Object exists. Keep the fallback bounded: taskkill itself
        # must never hang cleanup indefinitely. A failed Job close also reaches this path.
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=1)
        except Exception:
            pass


def _same_path(left: Path, right: Path) -> bool:
    """Compare local transport paths without requiring either path to exist."""
    return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(
        os.path.abspath(os.fspath(right))
    )


def run_bounded_cli(
    argv: list[str],
    *,
    prompt: str,
    cwd: str,
    timeout_s: float,
    stdout_path: Path,
    stderr_path: Path,
    watched_paths: Mapping[str, Path],
    max_bytes: int,
    error_type: Type[RuntimeError],
    label: str,
) -> int:
    """Run one CLI and return its exit status, enforcing strict stream and tree bounds.

    ``stdout`` and ``stderr`` are drained through bounded pipes instead of being handed directly to
    the child as regular files.  A polling-only file limit can overshoot by hundreds of megabytes in
    one scheduler interval; pipe backpressure keeps the on-disk capture at ``max_bytes`` even for a
    producer that writes as fast as the kernel accepts data.
    """
    if not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
        raise error_type(f"{label} argv must contain non-NUL text arguments")
    if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s) or timeout_s <= 0):
        raise error_type(f"{label} timeout must be a positive finite number")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise error_type(f"{label} max_bytes must be a positive integer")
    if not isinstance(prompt, str):
        raise error_type(f"{label} prompt must be text")
    try:
        prompt_bytes = prompt.encode("utf-8")
    except UnicodeError as exc:
        raise error_type(f"{label} prompt is not valid UTF-8 text: {exc}") from exc
    if len(prompt_bytes) > max_bytes:
        raise error_type(f"{label} prompt exceeded {max_bytes} bytes")
    prompt_path = Path(cwd) / "prompt.txt"
    if (_same_path(prompt_path, stdout_path) or _same_path(prompt_path, stderr_path)
            or _same_path(stdout_path, stderr_path)):
        raise error_type(f"{label} transport paths must be distinct")
    try:
        prompt_path.write_bytes(prompt_bytes)
    except OSError as exc:
        raise error_type(f"could not prepare {label} prompt: {exc}") from exc

    proc: subprocess.Popen | None = None
    job: WindowsMemoryJob | None = None
    cleanup_done = False
    readers: list[tuple[threading.Thread, BinaryIO]] = []
    issues: list[tuple[str, str, BaseException | None]] = []
    issues_lock = threading.Lock()
    wake = threading.Event()

    def report_issue(kind: str, stream: str, exc: BaseException | None = None) -> None:
        with issues_lock:
            if not issues:
                issues.append((kind, stream, exc))
        wake.set()

    def raise_stream_issue() -> None:
        with issues_lock:
            issue = issues[0] if issues else None
        if issue is None:
            return
        kind, stream, exc = issue
        if kind == "limit":
            raise error_type(f"{label} {stream} exceeded {max_bytes} bytes")
        if kind == "reader-stuck":
            raise error_type(f"{label} {stream} reader did not stop during cleanup")
        raise error_type(f"could not capture {label} {stream}: {exc}") from exc

    def drain(stream: str, source: BinaryIO, sink: BinaryIO) -> None:
        written = 0
        try:
            while True:
                chunk = os.read(source.fileno(), _READ_CHUNK_BYTES)
                if not chunk:
                    sink.flush()
                    return
                remaining = max_bytes - written
                if len(chunk) > remaining:
                    if remaining:
                        sink.write(chunk[:remaining])
                        written += remaining
                    sink.flush()
                    report_issue("limit", stream)
                    # Stop draining. The bounded kernel pipe now applies backpressure until the main
                    # thread tears down the whole process tree.
                    return
                sink.write(chunk)
                written += len(chunk)
        except (OSError, ValueError) as exc:
            report_issue("io", stream, exc)

    def inspect_watched_paths() -> None:
        for stream, path in watched_paths.items():
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                size = 0
            except OSError as exc:
                raise error_type(f"could not inspect {label} {stream}: {exc}") from exc
            if size > max_bytes:
                raise error_type(f"{label} {stream} exceeded {max_bytes} bytes")

    try:
        with (
            prompt_path.open("rb") as stdin_file,
            stdout_path.open("wb") as stdout_file,
            stderr_path.open("wb") as stderr_file,
        ):
            kwargs = {
                "stdin": stdin_file,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "cwd": cwd,
                "start_new_session": os.name != "nt",
            }
            try:
                if os.name == "nt":
                    kwargs["creationflags"] = subprocess.CREATE_SUSPENDED
                proc = subprocess.Popen(argv, **kwargs)
                if os.name == "nt":
                    # Suspended launch closes the root-PID race: no descendant can escape before the
                    # root belongs to the kill-on-close Job Object.
                    job = WindowsMemoryJob(proc)
                    resume_suspended_windows_process(proc)
            except OSError as exc:
                raise error_type(f"could not start {label}: {exc}") from exc
            except Exception as exc:
                raise error_type(f"could not contain {label} process tree: {exc}") from exc

            returncode: int | None = None
            try:
                assert proc.stdout is not None and proc.stderr is not None
                for stream, source, sink in (
                    ("stdout", proc.stdout, stdout_file),
                    ("stderr", proc.stderr, stderr_file),
                ):
                    reader = threading.Thread(
                        target=drain,
                        args=(stream, source, sink),
                        name=f"mathagent-{label.lower().replace(' ', '-')}-{stream}-{proc.pid}",
                        daemon=True,
                    )
                    reader.start()
                    readers.append((reader, source))

                deadline = time.monotonic() + timeout_s
                while proc.poll() is None:
                    raise_stream_issue()
                    inspect_watched_paths()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise error_type(f"{label} timed out after {timeout_s}s")
                    wake.wait(min(_POLL_INTERVAL_S, remaining))
                returncode = int(proc.returncode)
                inspect_watched_paths()
            finally:
                # Kill the containment unit before joining readers. A launcher may have exited while
                # a descendant still holds either pipe open.
                _terminate_tree(proc, job)
                cleanup_done = True
                join_deadline = time.monotonic() + 5
                for reader, _source in readers:
                    reader.join(max(0.0, join_deadline - time.monotonic()))
                stuck = [(reader, source) for reader, source in readers if reader.is_alive()]
                if stuck:
                    for _reader, source in stuck:
                        try:
                            source.close()
                        except OSError:
                            pass
                    for reader, _source in stuck:
                        reader.join(1)
                        if reader.is_alive():
                            report_issue("reader-stuck", reader.name)
                for source in (proc.stdout, proc.stderr):
                    if source is None:
                        continue
                    try:
                        source.close()
                    except (OSError, ValueError):
                        pass

            raise_stream_issue()
            assert returncode is not None
            return returncode
    except error_type:
        raise
    except OSError as exc:
        raise error_type(f"{label} transport failed: {exc}") from exc
    except Exception as exc:
        raise error_type(f"{label} transport failed: {exc}") from exc
    finally:
        # Also covers failures before the reader lifecycle starts (for example Job assignment/resume).
        if proc is not None and not cleanup_done:
            _terminate_tree(proc, job)

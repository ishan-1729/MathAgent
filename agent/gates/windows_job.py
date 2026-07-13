"""Windows Job Object containment shared by one-shot and persistent Lean processes."""
from __future__ import annotations


class WindowsMemoryJob:
    """Kill-on-close process-tree container with an optional aggregate memory ceiling."""

    _KILL_ON_CLOSE = 0x00002000
    _JOB_MEMORY = 0x00000200
    _EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self, proc, memory_mb: int | None = None):
        import ctypes
        from ctypes import wintypes

        if memory_mb is not None:
            if isinstance(memory_mb, bool) or not isinstance(memory_mb, int) or memory_mb <= 0:
                raise ValueError("memory_mb must be a positive integer or None")
            max_size_t = (1 << (ctypes.sizeof(ctypes.c_size_t) * 8)) - 1
            if memory_mb > max_size_t // (1024 * 1024):
                raise ValueError("memory_mb is too large for the Windows Job Object API")

        class _BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = self._KILL_ON_CLOSE
        if memory_mb is not None:
            limits.BasicLimitInformation.LimitFlags |= self._JOB_MEMORY
            limits.JobMemoryLimit = memory_mb * 1024 * 1024
        if not kernel32.SetInformationJobObject(
                handle, self._EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits),
                ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            kernel32.CloseHandle(handle)
            raise error
        if not kernel32.AssignProcessToJobObject(handle, wintypes.HANDLE(int(proc._handle))):
            error = ctypes.WinError(ctypes.get_last_error())
            kernel32.CloseHandle(handle)
            raise error
        self._kernel32 = kernel32
        self._handle = handle

    def close(self) -> None:
        """Terminate the contained tree and release the job handle.

        ``KILL_ON_JOB_CLOSE`` remains the inherited fail-safe, while an explicit termination avoids
        relying solely on a successful final ``CloseHandle`` call. A close failure is retained and
        reported, with the termination outcome attached, so callers can retry handle release and use
        a root-process fallback only when the tree was not already terminated.
        """
        import ctypes

        handle = self._handle
        if not handle:
            return
        terminated = bool(self._kernel32.TerminateJobObject(handle, 1))
        closed = bool(self._kernel32.CloseHandle(handle))
        close_error = ctypes.get_last_error() if not closed else 0
        if closed:
            self._handle = None
        if not closed:
            error = ctypes.WinError(close_error)
            # Lets the process transport avoid a stale-root-PID `taskkill` after the Job API already
            # killed every member, while still reporting/retaining the leaked handle for retry.
            error.tree_terminated = terminated
            raise error
        # If termination failed but handle close succeeded, KILL_ON_JOB_CLOSE still provided the
        # containment guarantee. The explicit call is defense-in-depth, not a second requirement.

    def __del__(self) -> None:
        """Best-effort last resort for an exceptional ``CloseHandle`` failure."""
        try:
            handle = getattr(self, "_handle", None)
            kernel32 = getattr(self, "_kernel32", None)
            if handle and kernel32 is not None and kernel32.CloseHandle(handle):
                self._handle = None
        except Exception:
            pass


def resume_suspended_windows_process(proc) -> None:
    """Resume a suspended process only after it has entered its Job Object."""
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    status = ntdll.NtResumeProcess(wintypes.HANDLE(int(proc._handle)))
    if status != 0:
        raise OSError(f"NtResumeProcess failed with NTSTATUS 0x{status & 0xffffffff:08x}")

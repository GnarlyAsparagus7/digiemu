"""Keep the +Drive card file sparse on Windows, where Python alone cannot.

The card is a ~950 MB file of which a finished first boot writes a few MB:
the ekFS format (~2 MB), the factory project and sounds (~3.9 MB). Before
this module it took 927,400 KB on NTFS, for two reasons measured on
Windows 11 / NTFS (2026-09-23; HANDOFF-2026-09-23.md, the portable app):

  * Python's file.truncate() on Windows goes through the C runtime's
    _chsize_s, which EXTENDS a file by writing zeros. They are cached
    writes, so the file looks unallocated for a second or two and then the
    lazy writer allocates every byte -- even on a file marked sparse.
    SetEndOfFile moves end-of-file without writing anything, and a sparse
    file extended that way stays a hole until something is written there.
  * A first boot ERASES 512 MB of the card (the project region), and
    Card.flush made that real by writing zeros over it. FSCTL_SET_ZERO_DATA
    on a sparse file deallocates the range instead, and it works with the
    card's memory map open (the map reads zeros straight afterwards).

Memory-mapping a sparse file does not allocate it, read-only or writable;
only the pages actually written are (in 64 KB units).

Everything here is best effort and silent: on another filesystem (FAT32 or
exFAT on a USB stick), on POSIX, or if an ioctl fails, the caller gets False
and falls back to what it did before -- a file that is correct but not
sparse. Nothing here may change what the card reads back. Standard library
only; no Unicorn.
"""
import os
import stat

_WINDOWS = os.name == 'nt'

FSCTL_SET_SPARSE = 0x000900C4
FSCTL_SET_ZERO_DATA = 0x000980C8

_k32 = None


def _kernel32():
    """-> kernel32 with the three prototypes this module calls, or None."""
    global _k32
    if not _WINDOWS:
        return None
    if _k32 is None:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.DeviceIoControl.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID]
        k32.DeviceIoControl.restype = wintypes.BOOL
        k32.SetFilePointerEx.argtypes = [
            wintypes.HANDLE, ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD]
        k32.SetFilePointerEx.restype = wintypes.BOOL
        k32.SetEndOfFile.argtypes = [wintypes.HANDLE]
        k32.SetEndOfFile.restype = wintypes.BOOL
        k32.GetCompressedFileSizeW.argtypes = [
            wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        k32.GetCompressedFileSizeW.restype = wintypes.DWORD
        _k32 = k32
    return _k32


def _handle(fh):
    import msvcrt
    return msvcrt.get_osfhandle(fh.fileno())


def _ioctl(fh, code, inbuf=None):
    import ctypes
    from ctypes import wintypes
    k32 = _kernel32()
    done = wintypes.DWORD(0)
    if inbuf is None:
        ok = k32.DeviceIoControl(_handle(fh), code, None, 0, None, 0,
                                 ctypes.byref(done), None)
    else:
        ok = k32.DeviceIoControl(_handle(fh), code, ctypes.byref(inbuf),
                                 ctypes.sizeof(inbuf), None, 0,
                                 ctypes.byref(done), None)
    return bool(ok)


def make_sparse(fh):
    """Mark the open file sparse. -> True if it now is (NTFS, ReFS)."""
    if not _WINDOWS:
        return False
    try:
        fh.flush()
        return _ioctl(fh, FSCTL_SET_SPARSE)
    except (OSError, ValueError, AttributeError):
        return False


def is_sparse(fh_or_path):
    """-> True if the file (an open file or a path) is marked sparse."""
    if not _WINDOWS:
        return False
    try:
        if isinstance(fh_or_path, (str, bytes, os.PathLike)):
            st = os.stat(fh_or_path)
        else:
            st = os.fstat(fh_or_path.fileno())
    except (OSError, ValueError):
        return False
    attrs = getattr(st, 'st_file_attributes', 0)
    return bool(attrs & stat.FILE_ATTRIBUTE_SPARSE_FILE)


def extend(fh, size):
    """Set the open file's length to `size` without writing any data.

    On Windows this is SetEndOfFile (see the module docstring for why not
    fh.truncate); elsewhere ftruncate, which leaves a hole by itself. The
    file position is left at 0.
    """
    fh.flush()
    if _WINDOWS:
        try:
            k32 = _kernel32()
            h = _handle(fh)
            if k32.SetFilePointerEx(h, int(size), None, 0) \
                    and k32.SetEndOfFile(h):
                fh.seek(0)
                return
        except (OSError, ValueError, AttributeError):
            pass
    fh.truncate(size)
    fh.seek(0)


def zero_range(fh, lo, hi):
    """Deallocate [lo, hi) of a SPARSE file; it then reads as zeros.

    -> True if done. False (and nothing changed) when the file is not sparse
    or the call is not available, and the caller writes the zeros itself.
    """
    if hi <= lo or not _WINDOWS or not is_sparse(fh):
        return False
    try:
        import ctypes

        class _ZeroData(ctypes.Structure):
            _fields_ = [('FileOffset', ctypes.c_longlong),
                        ('BeyondFinalZero', ctypes.c_longlong)]

        fh.flush()
        return _ioctl(fh, FSCTL_SET_ZERO_DATA, _ZeroData(int(lo), int(hi)))
    except (OSError, ValueError, AttributeError):
        return False


def allocated_size(path):
    """-> bytes the file occupies on disk (GetCompressedFileSizeW; on POSIX
    st_blocks * 512), or None if it cannot be told."""
    if _WINDOWS:
        import ctypes
        from ctypes import wintypes
        k32 = _kernel32()
        high = wintypes.DWORD(0)
        ctypes.set_last_error(0)
        low = k32.GetCompressedFileSizeW(os.fspath(path), ctypes.byref(high))
        if low == 0xFFFFFFFF and ctypes.get_last_error():
            return None
        return (high.value << 32) | low
    try:
        return os.stat(path).st_blocks * 512
    except (OSError, AttributeError):
        return None


__all__ = ['allocated_size', 'extend', 'is_sparse', 'make_sparse',
           'zero_range']

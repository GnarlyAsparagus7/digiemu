"""Native accelerators in the patched Unicorn, when the build has them.

patches/unicorn-2.1.4-m68k-digikit-accel.patch adds two things to libunicorn
that the emulator otherwise does in Python hooks, where every crossing costs
microseconds:

  * uc_digikit_add_budget_hook: a block hook that ends emu_start after N
    translation blocks -- longrun._FastStepper's budget, without a Python
    call on every block (measured: a Python block hook alone holds a loop to
    ~10M instructions/s);
  * uc_digikit_edma_install: the software-started eDMA channels the audio
    render starts and polls (emu/edma_sw.py is the Python original, still
    used for the rare transfer that needs a page mapped first).

patches/unicorn-2.1.4-m68k-digikit-speed.patch adds engine options
(uc_digikit_set_options), both off unless asked for:

  * NATIVE_RTE: `rte` is done by QEMU's own ColdFire return instead of the
    Python UC_HOOK_INTR in Machine.install_exceptions -- the same frame pop,
    several thousand times a second without a crossing;
  * NO_MEM_EXIT: no exit-request check after every guest load and store.
    Those exist so a memory hook that stops emulation stops at that access;
    none of this emulator's memory hooks stops emulation, and the check is a
    helper call on every access (about a tenth of a live-audio run).
  * NO_HOOK_PC_SYNC: memory read/write hooks see the PC as last synced
    rather than an exact PC rebuilt (by re-translating the block) for every
    hooked access. Only for callers whose memory hooks never read the PC --
    the live-audio GUI turns it on (emu/gui.py).

It also fuses each MAC into one helper call; that needs no option.

Everything here degrades: on a Unicorn without the patch `available()` is
False and callers keep their Python paths.
"""
from __future__ import annotations

import ctypes

try:
    from unicorn.unicorn_py3.unicorn import uclib as _uclib
except Exception:                                   # noqa: BLE001
    _uclib = None

CHANNELS = 64


class Budget(ctypes.Structure):
    _fields_ = [('left', ctypes.c_int64), ('blocks', ctypes.c_int64)]


class Edma(ctypes.Structure):
    """Mirror of struct uc_digikit_edma (checked against its C size)."""
    _fields_ = [('tcd_base', ctypes.c_uint32),
                ('map_page', ctypes.c_uint32),
                ('claimed', ctypes.c_uint64),
                ('pending', ctypes.c_uint16 * CHANNELS),
                ('deferred', ctypes.c_uint8 * CHANNELS),
                ('any_deferred', ctypes.c_uint8),
                ('pad', ctypes.c_uint8 * 7),
                ('transfers', ctypes.c_uint64 * CHANNELS),
                ('links', ctypes.c_uint64 * CHANNELS),
                ('bytes', ctypes.c_uint64 * CHANNELS),
                ('refused', ctypes.c_uint64 * CHANNELS),
                ('ndeferred', ctypes.c_uint64 * CHANNELS),
                ('links_ignored', ctypes.c_uint64 * CHANNELS)]


def _fn(name, restype, argtypes):
    f = getattr(_uclib, name, None) if _uclib is not None else None
    if f is not None:
        f.restype = restype
        f.argtypes = argtypes
    return f


_add_budget = _fn('uc_digikit_add_budget_hook', ctypes.c_int,
                  [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                   ctypes.POINTER(Budget)])
_edma_install = _fn('uc_digikit_edma_install', ctypes.c_int,
                    [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                     ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(Edma)])
_edma_size = _fn('uc_digikit_edma_size', ctypes.c_size_t, [])
_hook_del = _fn('uc_hook_del', ctypes.c_int, [ctypes.c_void_p, ctypes.c_size_t])
_options_supported = _fn('uc_digikit_options_supported', ctypes.c_uint32, [])
_set_options = _fn('uc_digikit_set_options', ctypes.c_int,
                   [ctypes.c_void_p, ctypes.c_uint32])

NATIVE_RTE = 1
NO_MEM_EXIT = 2
NO_HOOK_PC_SYNC = 4


def _handle(uc):
    """The uc_engine* of a unicorn.Uc, or None for anything else."""
    h = getattr(uc, '_uch', None)
    return h if isinstance(h, ctypes.c_void_p) else None


def budget_available(uc):
    return _add_budget is not None and _handle(uc) is not None


def edma_available(uc):
    return (_edma_install is not None and _handle(uc) is not None
            and _edma_size is not None
            and _edma_size() == ctypes.sizeof(Edma))


def options_supported():
    """-> the speed options (NATIVE_RTE | NO_MEM_EXIT) this library has."""
    return _options_supported() if _options_supported is not None else 0


def enable_options(uc, options):
    """Turn on `options` on this engine, in addition to any already on.
    -> the options now on (0 if the library has none). Unsupported bits are
    ignored, so callers can ask for what they want and read back what they
    got."""
    handle = _handle(uc)
    if handle is None or _set_options is None:
        return 0
    want = (getattr(uc, '_digikit_options', 0) | options) & options_supported()
    err = _set_options(handle, want)
    if err:
        raise RuntimeError('uc_digikit_set_options failed: %d' % err)
    uc._digikit_options = want
    return want


class NativeBudget:
    """A native block budget on one engine. Keep the object alive."""

    def __init__(self, uc):
        self.state = Budget()
        self.hook = ctypes.c_size_t()
        err = _add_budget(_handle(uc), ctypes.byref(self.hook),
                          ctypes.byref(self.state))
        if err:
            raise RuntimeError('uc_digikit_add_budget_hook failed: %d' % err)


class NativeEdma:
    """The native software eDMA on one engine. Keep the object alive."""

    def __init__(self, uc, tcd_base, map_page, claimed):
        self.state = Edma()
        self.state.tcd_base = tcd_base
        self.state.map_page = map_page
        mask = 0
        for ch in claimed:
            mask |= 1 << ch
        self.state.claimed = mask
        self.hooks = (ctypes.c_size_t(), ctypes.c_size_t())
        err = _edma_install(_handle(uc), ctypes.byref(self.hooks[0]),
                            ctypes.byref(self.hooks[1]),
                            ctypes.byref(self.state))
        if err:
            raise RuntimeError('uc_digikit_edma_install failed: %d' % err)

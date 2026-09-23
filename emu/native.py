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

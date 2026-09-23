# pyright: reportMissingImports=false
"""Leaner register and memory reads for one Unicorn engine.

The live-audio GUI crosses from Python into Unicorn some hundreds of thousands
of times a second: every `rte` is implemented in a Python hook (three register
writes, a register read, a memory read), every interrupt delivered pushes a
frame from Python, and every step boundary reads PC. The binding spends more
time on each of those calls than the engine does -- `reg_read` looks up the
register class, builds a fresh ctypes value and wraps it in `byref` every
time; `mem_read` allocates a string buffer per call.

`install(uc)` replaces `reg_read`, `reg_write` and `mem_read` on that one
instance with versions that make the same `uc_*` calls into buffers reused
across calls. Same values, same `UcError`s, and anything the fast path does
not cover (an `aux` argument, a large read) goes to the original method. It
only applies where every register is the binding's generic 64-bit class,
which is what m68k is, and is a no-op otherwise.

The buffers are shared per engine, so the engine must be driven from one
thread, as it already is (the GUI's window thread never touches it).
"""
import ctypes

# Reads up to this size reuse a buffer; larger ones allocate as before.
_REUSE_MAX = 4096


def install(uc):
    """Speed up `uc`'s register and memory reads in place. -> True if done."""
    try:
        from unicorn import UcError
        from unicorn.unicorn_py3.unicorn import uclib
        if uc._select_reg_class(0) is not ctypes.c_uint64:
            return False
        handle = uc._uch
        c_reg_read, c_reg_write = uclib.uc_reg_read, uclib.uc_reg_write
        c_mem_read = uclib.uc_mem_read
    except Exception:                                  # noqa: BLE001
        return False
    orig_reg_read, orig_mem_read = uc.reg_read, uc.mem_read
    value = ctypes.c_uint64()
    ref = ctypes.byref(value)
    bufs = {}

    def reg_read(reg_id, aux=None):
        if aux is not None:
            return orig_reg_read(reg_id, aux)
        # A 32-bit register fills only the low half of the value; the
        # binding's fresh c_uint64 starts at zero, so this one must too.
        value.value = 0
        status = c_reg_read(handle, reg_id, ref)
        if status:
            raise UcError(status, reg_id)
        return value.value

    def reg_write(reg_id, val):
        value.value = val
        status = c_reg_write(handle, reg_id, ref)
        if status:
            raise UcError(status, reg_id)

    def mem_read(address, size):
        if size > _REUSE_MAX:
            return orig_mem_read(address, size)
        buf = bufs.get(size)
        if buf is None:
            buf = bufs[size] = ctypes.create_string_buffer(size)
        status = c_mem_read(handle, address, buf, size)
        if status:
            raise UcError(status, address, size)
        return bytearray(buf.raw)

    uc.reg_read = reg_read
    uc.reg_write = reg_write
    uc.mem_read = mem_read
    return True

"""Digitakt (mk1) panel wiring: control code -> (channel, bit).

MEASURED, not derived. `emu/panelin.py`'s `code_for()` computes
`channel*8 + bit + 1` and documents that as holding on Digitakt II and
Digitone II. It does NOT hold on Digitakt (mk1): the panel MCU scans this
product's matrix in a different order, so the arithmetic silently addresses
the wrong key. Sending what arithmetic called PLAY actually pressed trig 8.

Produced by tools/mapsweep.py, which presses every (channel, bit) and reads
the code byte out of the record the firmware hands to `queue_send` -- the
firmware's own classification, not an inference from what the screen did.

Channel 0 is the transport and page column, channel 1 the cursor cluster,
channels 2-4 the sixteen trig keys interleaved with the mode buttons, and
channel 5 the encoder pushes. The trig keys are NOT contiguous.
"""

# control code (index into the firmware's 48-entry name table) -> (channel, bit)
CODE_TO_WIRE = {
    24: (0, 0), 25: (0, 1), 26: (0, 2), 27: (0, 3),
    28: (0, 4), 29: (0, 5), 30: (0, 6), 31: (0, 7),
    32: (1, 0), 33: (1, 1), 34: (1, 2), 35: (1, 3),
    36: (1, 4), 37: (1, 5), 38: (1, 6), 39: (1, 7),
    2: (2, 0), 1: (2, 1), 19: (2, 2), 20: (2, 3),
    0: (2, 4), 21: (2, 5), 22: (2, 6), 23: (2, 7),
    6: (3, 0), 7: (3, 1), 8: (3, 2), 13: (3, 3),
    16: (3, 4), 15: (3, 5), 17: (3, 6), 18: (3, 7),
    4: (4, 0), 3: (4, 1), 5: (4, 2), 9: (4, 3),
    10: (4, 4), 11: (4, 5), 12: (4, 6), 14: (4, 7),
    46: (5, 0), 45: (5, 1), 44: (5, 2), 41: (5, 3),
    40: (5, 4), 43: (5, 5), 42: (5, 6), 47: (5, 7),
}

WIRE_TO_CODE = {wire: code for code, wire in CODE_TO_WIRE.items()}


def wire_for(code):
    """-> (channel, bit) for a control code, or None if this panel has none."""
    return CODE_TO_WIRE.get(code)


def names(img, base=0x4018e254, load=0x40000400):
    """-> {NAME: code} read from the firmware's own control-name table."""
    import struct
    out = {}
    for code in range(48):
        off = base - load + 4 * code
        if off + 4 > len(img):
            break
        ptr = struct.unpack_from('>I', img, off)[0]
        o = ptr - load
        if not (0 <= o < len(img)):
            break
        end = img.find(b'\x00', o)
        if end < 0 or end - o > 24:
            break
        out[img[o:end].decode('latin1').upper()] = code
    return out

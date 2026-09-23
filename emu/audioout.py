"""Host audio output for the emulated codec stream.

The SSI transmit model (`emu/ssi.py`) hands its `sink` every byte the
transmit DMA moves: on Digitakt mk1, stereo pairs of big-endian 32-bit words.
`frames_from_ssi` turns those into 16-bit little-endian stereo, and a player
queues them to the host.

`WaveOut` is Windows `winmm` through ctypes -- nothing to install. Elsewhere,
or when no device opens, `WavFile` records the same stream to a file so the
output can still be checked by ear afterwards.

The emulator runs slower than real time, so the stream arrives in bursts
with gaps between them. The player never blocks the emulator thread: a block
that finds every buffer still playing is dropped and counted, which is
audible as a gap rather than a stall.
"""
from __future__ import annotations

import array
import ctypes
import struct
import sys
import threading
import time
import wave

WAVE_MAPPER = 0xFFFFFFFF
WAVE_FORMAT_PCM = 1
CALLBACK_NULL = 0
WHDR_DONE = 0x00000001
MMSYSERR_NOERROR = 0


def frames_from_ssi(data, word_bits=32, sample_bits=24):
    """Big-endian SSI words, left/right interleaved -> 16-bit LE stereo.

    `sample_bits` is how many of each `word_bits` word carry the sample,
    counted from the least significant bit (right-justified, the SSI's
    default). The top 16 of those bits are kept.
    """
    step = word_bits // 8
    count = len(data) // step
    if word_bits == 32 and sample_bits == 24:
        # The live-audio case, a few thousand times a second. Bits 23..8 of
        # a big-endian word are its bytes 1 and 2, and taken as a signed
        # 16-bit value they are exactly the 24-bit sample shifted right by 8
        # (the sign bit is among them). So: those two bytes, little-endian.
        data = bytes(data[:count * 4])
        out = bytearray(2 * count)
        out[0::2] = data[2::4]
        out[1::2] = data[1::4]
        return bytes(out)
    words = struct.unpack('>%d%s' % (count, 'I' if step == 4 else 'H'),
                          data[:count * step])
    shift = sample_bits - 16
    sign = 1 << (sample_bits - 1)
    mask = (1 << sample_bits) - 1
    out = []
    for w in words:
        v = w & mask
        if v & sign:
            v -= 1 << sample_bits
        out.append(v >> shift if shift >= 0 else v << -shift)
    return struct.pack('<%dh' % len(out), *out)


class _WaveFormatEx(ctypes.Structure):
    _fields_ = [('wFormatTag', ctypes.c_ushort),
                ('nChannels', ctypes.c_ushort),
                ('nSamplesPerSec', ctypes.c_uint),
                ('nAvgBytesPerSec', ctypes.c_uint),
                ('nBlockAlign', ctypes.c_ushort),
                ('wBitsPerSample', ctypes.c_ushort),
                ('cbSize', ctypes.c_ushort)]


class _WaveHdr(ctypes.Structure):
    pass


_WaveHdr._fields_ = [('lpData', ctypes.c_void_p),
                     ('dwBufferLength', ctypes.c_uint),
                     ('dwBytesRecorded', ctypes.c_uint),
                     ('dwUser', ctypes.c_size_t),
                     ('dwFlags', ctypes.c_uint),
                     ('dwLoops', ctypes.c_uint),
                     ('lpNext', ctypes.POINTER(_WaveHdr)),
                     ('reserved', ctypes.c_size_t)]


class WaveOut:
    """Queue 16-bit stereo PCM to the default Windows output device."""

    def __init__(self, rate=48000, channels=2, buffers=16, block_ms=20):
        if sys.platform != 'win32':
            raise OSError('WaveOut needs Windows')
        self.rate, self.channels = rate, channels
        self.frame = 2 * channels
        self.block = max(self.frame,
                         rate * block_ms // 1000 * self.frame)
        self.dropped = 0
        self.played = 0
        self._pending = bytearray()
        self._winmm = ctypes.WinDLL('winmm')
        fmt = _WaveFormatEx(WAVE_FORMAT_PCM, channels, rate,
                            rate * self.frame, self.frame, 16, 0)
        self._handle = ctypes.c_void_p()
        err = self._winmm.waveOutOpen(ctypes.byref(self._handle), WAVE_MAPPER,
                                      ctypes.byref(fmt), None, None,
                                      CALLBACK_NULL)
        if err != MMSYSERR_NOERROR:
            raise OSError('waveOutOpen failed: MMSYSERR %d' % err)
        self._bufs = [ctypes.create_string_buffer(self.block)
                      for _ in range(buffers)]
        self._hdrs = [_WaveHdr() for _ in range(buffers)]
        for buf, hdr in zip(self._bufs, self._hdrs):
            hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
            hdr.dwBufferLength = self.block
            hdr.dwFlags = 0
            self._winmm.waveOutPrepareHeader(self._handle, ctypes.byref(hdr),
                                             ctypes.sizeof(hdr))
            hdr.dwFlags |= WHDR_DONE          # free until first written

    def _free(self):
        for i, hdr in enumerate(self._hdrs):
            if hdr.dwFlags & WHDR_DONE:
                return i
        return None

    def queued(self):
        """Blocks handed to the device and not yet played."""
        return sum(1 for h in self._hdrs if not h.dwFlags & WHDR_DONE)

    def write(self, pcm, block=False, abort=None):
        """Append 16-bit LE PCM; full blocks go to the device at once.

        block=False drops a block that finds every buffer busy (live use:
        never stall the caller). block=True waits for a free buffer instead
        (playing a finished recording), until `abort()` says to give up.
        """
        self._pending += pcm
        while len(self._pending) >= self.block:
            if not self._submit(bytes(self._pending[:self.block]),
                                block, abort):
                return
            del self._pending[:self.block]

    def _submit(self, chunk, block, abort):
        """Queue one full block. -> False if abandoned (abort)."""
        i = self._free()
        while i is None and block:
            if abort is not None and abort():
                return False
            time.sleep(0.002)
            i = self._free()
        if i is None:
            self.dropped += 1
            return True
        hdr = self._hdrs[i]
        ctypes.memmove(self._bufs[i], chunk, self.block)
        hdr.dwFlags &= ~WHDR_DONE
        self._winmm.waveOutWrite(self._handle, ctypes.byref(hdr),
                                 ctypes.sizeof(hdr))
        self.played += 1
        return True

    def drain(self, abort=None):
        """Send any partial block, then wait until everything has played.

        The tail is padded with silence to a whole block: a prepared header
        keeps the length it was prepared with.
        """
        if self._pending:
            tail = bytes(self._pending).ljust(self.block, b'\0')
            self._pending = bytearray()
            self._submit(tail, True, abort)
        while self.queued():
            if abort is not None and abort():
                return
            time.sleep(0.005)

    def close(self):
        if self._handle is None:
            return
        self._winmm.waveOutReset(self._handle)
        for hdr in self._hdrs:
            self._winmm.waveOutUnprepareHeader(self._handle, ctypes.byref(hdr),
                                               ctypes.sizeof(hdr))
        self._winmm.waveOutClose(self._handle)
        self._handle = None


class WavFile:
    """The same interface, recording to a .wav file instead."""

    def __init__(self, path, rate=48000, channels=2):
        self.rate, self.channels = rate, channels
        self.dropped = 0
        self.played = 0
        self._w = wave.open(path, 'wb')
        self._w.setnchannels(channels)
        self._w.setsampwidth(2)
        self._w.setframerate(rate)

    def queued(self):
        return 0

    def write(self, pcm):
        self._w.writeframes(pcm)
        self.played += 1

    def close(self):
        if self._w is not None:
            self._w.close()
            self._w = None


def trim_silence(pcm, channels=2, threshold=8, rate=48000, pad_ms=10):
    """16-bit LE PCM with leading and trailing near-silence cut off.

    Keeps `pad_ms` either side of the first and last sample whose magnitude
    exceeds `threshold`. -> b'' when nothing does.
    """
    frame = 2 * channels
    n = len(pcm) // frame
    if not n:
        return b''
    samples = array.array('h', bytes(pcm[:n * frame]))
    if sys.byteorder == 'big':
        samples.byteswap()
    first = next((i for i in range(len(samples))
                  if abs(samples[i]) > threshold), None)
    if first is None:
        return b''
    last = next(i for i in range(len(samples) - 1, -1, -1)
                if abs(samples[i]) > threshold)
    pad = rate * pad_ms // 1000
    start = max(0, first // channels - pad)
    end = min(n, last // channels + 1 + pad)
    return bytes(pcm[start * frame:end * frame])


def write_wav(path, pcm, rate=48000, channels=2):
    with wave.open(path, 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


class Player:
    """Plays one finished recording at a time, on its own thread.

    A new play() stops the one before. Never touches the emulator: the
    caller hands it a copy of the PCM.
    """

    def __init__(self, rate=48000, channels=2):
        self.rate, self.channels = rate, channels
        self.error = None
        self._thread = None
        self._stop = threading.Event()

    @property
    def playing(self):
        return self._thread is not None and self._thread.is_alive()

    def play(self, pcm):
        self.stop()
        self._stop = threading.Event()
        stop = self._stop
        self._thread = threading.Thread(target=self._run, args=(pcm, stop),
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None

    def _run(self, pcm, stop):
        try:
            out = WaveOut(self.rate, self.channels)
        except OSError as exc:
            self.error = str(exc)
            return
        try:
            out.write(pcm, block=True, abort=stop.is_set)
            out.drain(abort=stop.is_set)
        finally:
            out.close()


def open_output(rate=48000, channels=2, fallback_path=None):
    """The host device if there is one, else a WAV file if a path is given."""
    try:
        return WaveOut(rate, channels)
    except OSError as exc:
        if fallback_path is None:
            raise
        print('[audio] no output device (%s); recording to %s'
              % (exc, fallback_path), flush=True)
        return WavFile(fallback_path, rate, channels)

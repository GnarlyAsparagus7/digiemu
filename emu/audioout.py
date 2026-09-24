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
import ctypes.util
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


class _WinMMOut:
    """Queue 16-bit stereo PCM to the default Windows output device."""

    def __init__(self, rate=48000, channels=2, buffers=16, block_ms=20):
        if sys.platform != 'win32':
            raise OSError('WaveOut needs Windows')
        self.rate, self.channels = rate, channels
        self.gain = 1.0
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
        pcm = apply_gain(pcm, self.gain)
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


class _AudioStreamBasicDescription(ctypes.Structure):
    _fields_ = [
        ('mSampleRate', ctypes.c_double),
        ('mFormatID', ctypes.c_uint32),
        ('mFormatFlags', ctypes.c_uint32),
        ('mBytesPerPacket', ctypes.c_uint32),
        ('mFramesPerPacket', ctypes.c_uint32),
        ('mBytesPerFrame', ctypes.c_uint32),
        ('mChannelsPerFrame', ctypes.c_uint32),
        ('mBitsPerChannel', ctypes.c_uint32),
        ('mReserved', ctypes.c_uint32),
    ]


class _AudioQueueBuffer(ctypes.Structure):
    _fields_ = [
        ('mAudioDataBytesCapacity', ctypes.c_uint32),
        ('mAudioData', ctypes.c_void_p),
        ('mAudioDataByteSize', ctypes.c_uint32),
        ('mUserData', ctypes.c_void_p),
        ('mPacketDescriptionCapacity', ctypes.c_uint32),
        ('mPacketDescriptions', ctypes.c_void_p),
        ('mPacketDescriptionCount', ctypes.c_uint32),
    ]


class _AudioQueueOut:
    """Queue 16-bit stereo PCM to the default macOS output device via AudioQueue."""

    def __init__(self, rate=48000, channels=2, buffers=16, block_ms=20):
        path = ctypes.util.find_library('AudioToolbox') or (
            '/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox'
        )
        try:
            self._lib = ctypes.CDLL(path)
        except OSError as exc:
            raise OSError('cannot load AudioToolbox: %s' % exc) from exc

        self.rate, self.channels = rate, channels
        self.frame = 2 * channels
        self.block = max(self.frame, rate * block_ms // 1000 * self.frame)
        self.buffers = buffers
        self.dropped = 0
        self.played = 0
        self.gain = 1.0
        self._pending = bytearray()
        self._started = False
        self._lock = threading.Lock()

        # kAudioFormatLinearPCM ('lpcm'), signed integer and packed: plain
        # interleaved 16-bit stereo, one frame per packet.
        fmt = _AudioStreamBasicDescription(
            float(rate), 0x6c70636d, (1 << 2) | (1 << 3), self.frame, 1,
            self.frame, channels, 16, 0
        )
        self._cb_type = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_AudioQueueBuffer)
        )
        self._cb = self._cb_type(self._on_buffer_done)
        self._aq = ctypes.c_void_p()
        err = self._lib.AudioQueueNewOutput(
            ctypes.byref(fmt), self._cb, None, None, None, 0, ctypes.byref(self._aq)
        )
        if err != 0:
            raise OSError('AudioQueueNewOutput failed: %d' % err)

        self._bufs = []
        self._buf_map = {}
        self._free_indices = []
        for i in range(buffers):
            buf = ctypes.POINTER(_AudioQueueBuffer)()
            err = self._lib.AudioQueueAllocateBuffer(self._aq, self.block, ctypes.byref(buf))
            if err != 0:
                self.close()
                raise OSError('AudioQueueAllocateBuffer failed: %d' % err)
            self._bufs.append(buf)
            self._buf_map[ctypes.addressof(buf.contents)] = i
            self._free_indices.append(i)

    def _on_buffer_done(self, user_data, aq, buf_ptr):
        addr = ctypes.addressof(buf_ptr.contents)
        idx = self._buf_map.get(addr)
        if idx is not None:
            with self._lock:
                self._free_indices.append(idx)

    def queued(self):
        """Blocks handed to the device and not yet played."""
        with self._lock:
            if not hasattr(self, '_bufs') or not self._bufs:
                return 0
            return len(self._bufs) - len(self._free_indices)

    def _free(self):
        with self._lock:
            if self._free_indices:
                return self._free_indices.pop(0)
            return None

    def write(self, pcm, block=False, abort=None):
        """Append 16-bit LE PCM; full blocks go to the device at once."""
        pcm = apply_gain(pcm, self.gain)
        self._pending += pcm
        while len(self._pending) >= self.block:
            if not self._submit(bytes(self._pending[:self.block]), block, abort):
                return
            del self._pending[:self.block]

    def _submit(self, chunk, block, abort):
        """Queue one full block. -> False if abandoned (abort)."""
        if not hasattr(self, '_aq') or self._aq is None:
            return False
        i = self._free()
        while i is None and block:
            if abort is not None and abort():
                return False
            time.sleep(0.002)
            i = self._free()
        if i is None:
            self.dropped += 1
            return True
        buf = self._bufs[i]
        ctypes.memmove(buf.contents.mAudioData, chunk, self.block)
        buf.contents.mAudioDataByteSize = self.block
        err = self._lib.AudioQueueEnqueueBuffer(self._aq, buf, 0, None)
        if err != 0:
            self.dropped += 1
            with self._lock:
                self._free_indices.append(i)
            return True
        if not self._started:
            self._lib.AudioQueueStart(self._aq, None)
            self._started = True
        self.played += 1
        return True

    def drain(self, abort=None):
        """Send any partial block, then wait until everything has played."""
        if self._pending:
            tail = bytes(self._pending).ljust(self.block, b'\0')
            self._pending = bytearray()
            self._submit(tail, True, abort)
        while self.queued():
            if abort is not None and abort():
                return
            time.sleep(0.005)

    def close(self):
        if not hasattr(self, '_aq') or self._aq is None:
            return
        aq = self._aq
        self._aq = None
        try:
            self._lib.AudioQueueStop(aq, True)
            self._lib.AudioQueueDispose(aq, True)
        except Exception:
            pass


class WaveOut:
    """Queue 16-bit stereo PCM to the default host output device."""

    def __new__(cls, rate=48000, channels=2, buffers=16, block_ms=20):
        if sys.platform == 'win32':
            return _WinMMOut(rate, channels, buffers, block_ms)
        if sys.platform == 'darwin':
            return _AudioQueueOut(rate, channels, buffers, block_ms)
        raise OSError('WaveOut needs Windows or macOS')


class WavFile:
    """The same interface, recording to a .wav file instead."""

    def __init__(self, path, rate=48000, channels=2):
        self.rate, self.channels = rate, channels
        self.gain = 1.0
        self.dropped = 0
        self.played = 0
        self._w = wave.open(path, 'wb')
        self._w.setnchannels(channels)
        self._w.setsampwidth(2)
        self._w.setframerate(rate)

    def queued(self):
        return 0

    def write(self, pcm):
        pcm = apply_gain(pcm, self.gain)
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


def apply_gain(pcm, gain):
    """Multiply 16-bit LE PCM by `gain`, clipping to int16 range.

    Used by the panel's Master Volume knob: the hardware's volume pot is
    analog (not in the firmware's code table), so the emulator applies the
    knob position as a software gain before the samples reach the host
    audio device.
    """
    if gain == 1.0 or not pcm:
        return pcm
    samples = array.array('h')
    samples.frombytes(pcm)
    if sys.byteorder == 'big':
        samples.byteswap()
    # Promote to int32 so a gain > 1.0 doesn't overflow before clipping.
    out = array.array('h', (max(-32768, min(32767, int(s * gain)))
                              for s in samples))
    if sys.byteorder == 'big':
        out.byteswap()
    return out.tobytes()


class Player:
    """Plays one finished recording at a time, on its own thread.

    A new play() stops the one before. Never touches the emulator: the
    caller hands it a copy of the PCM.
    """

    def __init__(self, rate=48000, channels=2):
        self.rate, self.channels = rate, channels
        self.gain = 1.0             # the panel's Master Volume
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
        # A tenth of a second at a time, so turning Master Volume during a
        # replay is heard at once rather than on the next PLAY.
        step = self.rate * self.channels * 2 // 10
        try:
            for i in range(0, len(pcm), step):
                if stop.is_set():
                    break
                out.gain = self.gain
                out.write(pcm[i:i + step], block=True, abort=stop.is_set)
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

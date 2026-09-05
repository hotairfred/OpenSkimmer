import ctypes
import cmath
import math
import os
import re
import tempfile
import unittest
import wave

import numpy as np
from scipy.signal import lfilter


HERE = os.path.dirname(__file__)
RATE = 192000


class RecordedIqIsolationTests(unittest.TestCase):
    """Replay deterministic recorded IQ through real native channel probes."""

    def setUp(self):
        path = os.path.join(HERE, 'libitila_scanner.so')
        if not os.path.exists(path):
            self.skipTest('run `make macos-native` first')
        self.lib = ctypes.CDLL(path)
        dp = ctypes.POINTER(ctypes.c_double)
        self.lib.itila_sc_create.restype = ctypes.c_void_p
        self.lib.itila_sc_create.argtypes = [
            ctypes.c_int, ctypes.c_double, ctypes.c_int, ctypes.c_double,
            ctypes.c_int, ctypes.c_int, ctypes.c_double,
            ctypes.c_double, ctypes.c_double, dp, ctypes.c_int, dp,
        ]
        self.lib.itila_sc_free.argtypes = [ctypes.c_void_p]
        self.lib.itila_sc_add_probe_bin.argtypes = [ctypes.c_void_p, ctypes.c_double]
        self.lib.itila_sc_add_probe_bin.restype = ctypes.c_int
        self.lib.itila_sc_feed_iq.argtypes = [ctypes.c_void_p, dp, dp, ctypes.c_int]
        self.lib.itila_sc_peek_env.argtypes = [
            ctypes.c_void_p, ctypes.c_double, dp, dp, ctypes.c_int]
        self.lib.itila_sc_peek_env.restype = ctypes.c_int

    def test_single_cw_recording_is_suppressed_at_12khz_alias(self):
        center = 7_022_500.0
        carrier = center + 12_000.0
        seconds = 3
        count = RATE * seconds
        t = np.arange(count, dtype=np.float64) / RATE
        # Deterministic 20 WPM-like keying.  The WAV round trip makes this a
        # real recorded-IQ replay rather than a direct floating-point impulse.
        hard_key = ((np.floor(t / 0.12).astype(np.int64) % 5) < 3).astype(np.float64)
        # A transmitter-shaped edge (about 5 ms), not an impossible square
        # RF envelope whose broadband clicks legitimately occupy every bin.
        alpha = math.exp(-1.0 / (RATE * 0.005))
        key = lfilter([1.0 - alpha], [1.0, -alpha], hard_key)
        phase = 2.0 * np.pi * 12_000.0 * t
        i24 = np.round(6_000_000.0 * key * np.cos(phase)).astype(np.int32)
        q24 = np.round(6_000_000.0 * key * np.sin(phase)).astype(np.int32)
        interleaved = np.empty(count * 2, dtype=np.int32)
        interleaved[0::2] = i24
        interleaved[1::2] = q24
        unsigned = interleaved.astype(np.int64) & 0xFFFFFF
        packed = np.empty((unsigned.size, 3), dtype=np.uint8)
        packed[:, 0] = unsigned & 0xFF
        packed[:, 1] = (unsigned >> 8) & 0xFF
        packed[:, 2] = (unsigned >> 16) & 0xFF

        with tempfile.NamedTemporaryFile(suffix='.wav') as tmp:
            with wave.open(tmp.name, 'wb') as wav:
                wav.setnchannels(2)
                wav.setsampwidth(3)
                wav.setframerate(RATE)
                wav.writeframes(packed.tobytes())
            with wave.open(tmp.name, 'rb') as wav:
                raw = np.frombuffer(wav.readframes(count), dtype=np.uint8)

        triples = raw.reshape(-1, 3).astype(np.int32)
        replay = triples[:, 0] | (triples[:, 1] << 8) | (triples[:, 2] << 16)
        replay = np.where(replay & 0x800000, replay - 0x1000000, replay)

        iq_i = np.ascontiguousarray(replay[0::2], dtype=np.float64)
        iq_q = np.ascontiguousarray(replay[1::2], dtype=np.float64)
        null = ctypes.POINTER(ctypes.c_double)()
        scanner = self.lib.itila_sc_create(
            RATE, center, 4, 100.0, 100, 4096, 50.0,
            center - 20_000, center + 20_000, null, 0, null)
        self.assertTrue(scanner)
        try:
            self.assertEqual(self.lib.itila_sc_add_probe_bin(scanner, carrier), 1)
            self.assertEqual(self.lib.itila_sc_add_probe_bin(scanner, center), 1)
            chunk = RATE // 5
            for start in range(0, count, chunk):
                a = np.ascontiguousarray(iq_i[start:start + chunk])
                b = np.ascontiguousarray(iq_q[start:start + chunk])
                self.lib.itila_sc_feed_iq(
                    scanner,
                    a.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
                    b.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), len(a))

            actual100 = np.empty(1000, dtype=np.float64)
            actual200 = np.empty(1000, dtype=np.float64)
            alias100 = np.empty(1000, dtype=np.float64)
            alias200 = np.empty(1000, dtype=np.float64)
            dp = ctypes.POINTER(ctypes.c_double)
            n_actual = self.lib.itila_sc_peek_env(
                scanner, carrier, actual100.ctypes.data_as(dp),
                actual200.ctypes.data_as(dp), 1000)
            n_alias = self.lib.itila_sc_peek_env(
                scanner, center, alias100.ctypes.data_as(dp),
                alias200.ctypes.data_as(dp), 1000)
            self.assertGreater(n_actual, 400)
            self.assertEqual(n_actual, n_alias)
            # Ignore FIR startup. Compare the 200 Hz channel actually used by
            # the broad ITILA path.  -80 dB amplitude is a 1e-4 ratio.
            actual_rms = math.sqrt(float(np.mean(actual200[100:n_actual] ** 2)))
            alias_rms = math.sqrt(float(np.mean(alias200[100:n_alias] ** 2)))
            self.assertGreater(actual_rms, 1000.0)
            self.assertLess(alias_rms / actual_rms, 1e-4)
        finally:
            self.lib.itila_sc_free(scanner)


class StageOneFilterTests(unittest.TestCase):
    def test_first_decimation_image_has_at_least_80db_rejection(self):
        with open(os.path.join(HERE, 'itila_fir_coeffs.h')) as source:
            text = source.read()
        match = re.search(
            r'static const double FIR_STAGE1\[\d+\] = \{(.*?)\};',
            text, re.S)
        self.assertIsNotNone(match)
        taps = [float(value) for value in re.findall(
            r'[-+]?\d+\.\d+e[-+]\d+', match.group(1))]

        def response(freq):
            return abs(sum(tap * cmath.exp(-2j * math.pi * freq * n / RATE)
                           for n, tap in enumerate(taps)))

        self.assertGreater(20 * math.log10(response(300.0)), -0.2)
        self.assertLess(20 * math.log10(response(12_000.0)), -80.0)


if __name__ == '__main__':
    unittest.main()

"""Unit tests for AudioDenoiser and HighPassFilter."""

import unittest
import numpy as np

from src.audio.denoise import AudioDenoiser, HighPassFilter

class TestAudioDenoiser(unittest.TestCase):
    def test_highpass_filter_attenuation(self):
        """Verify low-frequency motor rumble (30 hz) is attenuated more than voice (1000 hz)."""
        hpf = HighPassFilter(sample_rate=16000, cutoff_hz=80.0)
        t = np.linspace(0, 0.1, 1600, endpoint=False)

        # 30 Hz low rumble
        rumble = (np.sin(2 * np.pi * 30 * t) * 10000).astype(np.float32)
        out_rumble = hpf.process(rumble)
        hpf.reset()

        # 1000 Hz voice frequency
        voice = (np.sin(2 * np.pi * 1000 * t) * 10000).astype(np.float32)
        out_voice = hpf.process(voice)

        rumble_in_rk= float(np.sqrt(np.mean(rumble ** 2)))
        rumble_out_rk= float(np.sqrt(np.mean(out_rumble ** 2)))
        voice_in_rk= float(np.sqrt(np.mean(voice ** 2)))
        voice_out_rk= float(np.sqrt(np.mean(out_voice ** 2)))

        # 30Hz should be attenuated significantly compared to input
        self.assertTrue(rumble_out_rk < rumble_in_rk * 0.5)
        # 1000Hz voice should pass through with > 90% preservation
        self.assertTrue(voice_out_rk > voice_in_rk * 0.90)

    def test_denoiser_enabled_processing(self):
        """Test AudioDenoiser with 16-khz pcm bytes."""
        denoiser = AudioDenoiser(sample_rate=16000, enabled=True)
        t = np.linspace(0, 0.02, 320, endpoint=False)
        raw_samples = (np.sin(2 * np.pi * 500 * t) * 8000).astype(np.int16)
        in_bytes = raw_samples.tobytes()

        out_bytes = denoiser.process(in_bytes)
        self.assertEqual(len(out_bytes), len(in_bytes))

        out_samples = np.frombuffer(out_bytes, dtype=np.int16)
        self.assertTrue(np.max(np.abs(out_samples)) > 1000)

    def test_denoiser_disabled_passthrough(self):
        """Test disabled denoiser passes bytes unchanged."""
        denoiser = AudioDenoiser(sample_rate=16000, enabled=False)
        raw_bytes = b'\x00\x01\x02\x03' * 80
        out_bytes = denoiser.process(raw_bytes)
        self.assertEqual(out_bytes, raw_bytes)

if __name__ == '__main__':
    unittest.main()

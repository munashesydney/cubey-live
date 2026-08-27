"""Unit tests for AudioDenoiser and HighPassFilter."""

import unittest
from unittest.mock import patch

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

    def test_pyrnnoise_streaming_api_is_used(self):
        """The neural backend must use denoise_chunk, not a nonexistent process method."""
        instances = []

        class FakeRNNoise:
            def __init__(self, sample_rate):
                self.sample_rate = sample_rate
                self.received = []
                self.reset_count = 0
                instances.append(self)

            def denoise_chunk(self, chunk):
                self.received.append(chunk.copy())
                yield np.array([0.9], dtype=np.float32), chunk // 2

            def reset(self):
                self.reset_count += 1

        with patch('src.audio.denoise.PYRNNOISE_AVAILABLE', True), patch(
            'src.audio.denoise.PyRNNoise', FakeRNNoise
        ):
            denoiser = AudioDenoiser(
                sample_rate=16000,
                enabled=True,
                enable_highpass=False,
            )
            samples = np.full(320, 8000, dtype=np.int16)
            output = np.frombuffer(denoiser.process(samples.tobytes()), np.int16)
            denoiser.reset()

        self.assertEqual(denoiser.backend_name, 'pyrnnoise (Neural)')
        self.assertEqual(instances[0].sample_rate, 16000)
        self.assertEqual(instances[0].received[0].shape, (1, 320))
        np.testing.assert_array_equal(output, np.full(320, 4000, dtype=np.int16))
        self.assertEqual(instances[0].reset_count, 1)

    def test_pyrnnoise_failure_switches_to_fallback(self):
        """A runtime neural failure is visible and permanently enables the fallback."""
        class BrokenRNNoise:
            def __init__(self, sample_rate):
                pass

            def denoise_chunk(self, chunk):
                raise RuntimeError('broken backend')

        with patch('src.audio.denoise.PYRNNOISE_AVAILABLE', True), patch(
            'src.audio.denoise.PyRNNoise', BrokenRNNoise
        ):
            denoiser = AudioDenoiser(
                sample_rate=16000,
                enabled=True,
                enable_highpass=False,
            )
            samples = np.full(320, 50, dtype=np.int16)
            with self.assertLogs('src.audio.denoise', level='ERROR') as logs:
                output = denoiser.process(samples.tobytes())

        self.assertIsNone(denoiser._rnnoise_py)
        self.assertIn('pyrnnoise failed', denoiser.backend_name)
        self.assertTrue(any('switching to adaptive noise gate' in line for line in logs.output))
        self.assertEqual(len(output), len(samples.tobytes()))

    def test_native_backend_uses_correct_wrapper_name_at_48khz(self):
        """The direct librnnoise fallback can initialize at its native sample rate."""
        wrapper_instances = []

        class FakeWrapper:
            def __init__(self, lib_path):
                self.lib_path = lib_path
                wrapper_instances.append(self)

        with patch('src.audio.denoise.PYRNNOISE_AVAILABLE', False), patch(
            'src.audio.denoise._find_system_librnnoise', return_value='/tmp/librnnoise.so'
        ), patch('src.audio.denoise.RNNoiseCTypesWrapper', FakeWrapper):
            denoiser = AudioDenoiser(sample_rate=48000, enabled=True)

        self.assertEqual(wrapper_instances[0].lib_path, '/tmp/librnnoise.so')
        self.assertEqual(denoiser.backend_name, 'librnnoise (librnnoise.so)')

if __name__ == '__main__':
    unittest.main()

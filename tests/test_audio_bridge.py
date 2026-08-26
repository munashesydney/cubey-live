import asyncio
import numpy as np
import unittest

from src.audio.resample import (
    resample_pcm16,
    convert_pcm16_mono_to_device,
    convert_device_to_pcm16_mono,
)
from src.audio.player import AudioPlayer
from src.audio.recorder import AudioRecorder


class TestAudioBridge(unittest.TestCase):
    def test_convert_pcm16_mono_to_device_i2s_32bit(self):
        t = np.linspace(0, 0.01, 240, endpoint=False)
        orig_samples = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)
        raw_pcm16 = orig_samples.tobytes()

        device_bytes = convert_pcm16_mono_to_device(
            raw_pcm16,
            source_rate=24000,
            target_rate=48000,
            target_channels=2,
            target_dtype='int32',
        )

        self.assertEqual(len(device_bytes), 480 * 2 * 4)
        device_samples = np.frombuffer(device_bytes, dtype=np.int32).reshape(-1, 2)
        self.assertEqual(device_samples.shape, (480, 2))
        np.testing.assert_array_equal(device_samples[:, 0], device_samples[:, 1])
        self.assertTrue(np.max(np.abs(device_samples)) > 1000 * 65536)

    def test_convert_device_to_pcm16_mono_i2s_32bit(self):
        t = np.linspace(0, 0.01, 480, endpoint=False)
        audio_16 = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
        audio_32 = audio_16.astype(np.int32) << 16
        stereo_32 = np.column_stack([audio_32, audio_32])
        device_bytes = stereo_32.tobytes()

        gemini_bytes = convert_device_to_pcm16_mono(
            device_bytes,
            source_rate=48000,
            target_rate=16000,
            source_channels=2,
            source_dtype='int32',
        )

        self.assertEqual(len(gemini_bytes), 160 * 2)
        gemini_samples = np.frombuffer(gemini_bytes, dtype=np.int16)
        self.assertEqual(len(gemini_samples), 160)
        self.assertTrue(np.max(np.abs(gemini_samples)) > 5000)

    def test_audio_player_buffer_i2s(self):
        player = AudioPlayer(
            sample_rate=24000,
            channels=1,
            block_size=240,
            device_sample_rate=48000,
            device_channels=2,
            device_dtype='int32',
        )
        self.assertEqual(player.device_sample_rate, 48000)
        self.assertEqual(player.device_channels, 2)
        self.assertEqual(player.device_dtype, 'int32')
        self.assertEqual(player.bytes_per_sample, 4)

        t = np.linspace(0, 0.01, 240, endpoint=False)
        chunk = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16).tobytes()
        player.is_playing = True
        player.play_chunk(chunk)

        self.assertEqual(player._buffered_bytes, 3840)
        player.clear()
        self.assertEqual(player._buffered_bytes, 0)
        self.assertEqual(len(player._chunks), 0)

    def test_audio_recorder_dispatch_i2s(self):
        recorder = AudioRecorder(
            sample_rate=16000,
            channels=1,
            chunk_size=320,
            device_sample_rate=48000,
            device_channels=2,
            device_dtype='int32',
        )
        self.assertEqual(recorder.device_sample_rate, 48000)
        self.assertEqual(recorder.device_channels, 2)
        self.assertEqual(recorder.device_dtype, 'int32')

        loop = asyncio.new_event_loop()
        recorder._loop = loop
        recorder.is_recording = True

        t = np.linspace(0, 0.02, 960, endpoint=False)
        audio_16 = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16)
        audio_32 = audio_16.astype(np.int32) << 16
        stereo_32 = np.column_stack([audio_32, audio_32])
        indata = stereo_32.tobytes()

        recorder._audio_callback(indata, 960, {}, None)
        loop.stop()
        loop.run_forever()

        self.assertFalse(recorder.audio_queue.empty())
        packet = recorder.audio_queue.get_nowait()
        self.assertEqual(len(packet), 640)
        loop.close()

if __name__ == '__main__':
    unittest.main()

"""PCM resampling and format conversion utilities for audio device boundaries."""

import numpy as np


def resample_pcm16(
    data: bytes,
    source_rate: int,
    target_rate: int,
    channels: int = 1,
) -> bytes:
    """Resample interleaved signed 16-bit PCM while preserving frame alignment."""
    if not data or source_rate == target_rate:
        return data
    if source_rate <= 0 or target_rate <= 0 or channels <= 0:
        raise ValueError("sample rates and channels must be positive")

    samples = np.frombuffer(data, dtype=np.int16)
    frame_count = len(samples) // channels
    if frame_count < 2:
        return data
    frames = samples[: frame_count * channels].reshape(frame_count, channels)

    # Integer-ratio downsampling gets a cheap box filter, which avoids the
    # worst voice-band aliasing when a 48 kHz mic is reduced to 16 kHz.
    if source_rate > target_rate and source_rate % target_rate == 0:
        ratio = source_rate // target_rate
        usable = frame_count - frame_count % ratio
        if usable:
            reduced = frames[:usable].reshape(-1, ratio, channels).mean(axis=1)
            return np.clip(reduced, -32768, 32767).astype(np.int16).tobytes()

    output_frames = max(1, int(round(frame_count * target_rate / source_rate)))
    source_positions = np.arange(output_frames, dtype=np.float64) * (
        source_rate / target_rate
    )
    source_positions = np.minimum(source_positions, frame_count - 1)
    left = source_positions.astype(np.int64)
    right = np.minimum(left + 1, frame_count - 1)
    fraction = (source_positions - left)[:, None]
    float_frames = frames.astype(np.float32)
    output = float_frames[left] + (
        float_frames[right] - float_frames[left]
    ) * fraction
    return np.clip(output, -32768, 32767).astype(np.int16).tobytes()


def convert_pcm16_mono_to_device(
    data: bytes,
    source_rate: int = 24000,
    target_rate: int = 48000,
    target_channels: int = 2,
    target_dtype: str = "int32",
) -> bytes:
    """Convert mono 16-bit PCM (e.g. Gemini 24kHz) to hardware format (e.g. 48kHz stereo int32 / S32_LE)."""
    if not data:
        return b""

    # 1. Resample sample rate
    resampled_bytes = resample_pcm16(data, source_rate, target_rate, channels=1)
    samples = np.frombuffer(resampled_bytes, dtype=np.int16)
    if len(samples) == 0:
        return b""

    # 2. Channel expansion (mono -> stereo / multi-channel)
    if target_channels > 1:
        frames = np.repeat(samples[:, np.newaxis], target_channels, axis=1)
    else:
        frames = samples

    # 3. Bit depth / data type conversion
    dtype_lower = target_dtype.lower().strip()
    if dtype_lower in ("int32", "s32", "s32_le"):
        # Shift 16-bit sample into upper 16 bits of 32-bit container for I2S S32_LE DACs (MAX98357A)
        device_frames = (frames.astype(np.int32) << 16)
        return device_frames.tobytes()
    elif dtype_lower in ("float32", "f32"):
        device_frames = frames.astype(np.float32) / 32768.0
        return device_frames.tobytes()
    else:
        # Default int16 / s16
        return frames.astype(np.int16).tobytes()


def convert_device_to_pcm16_mono(
    data: bytes,
    source_rate: int = 48000,
    target_rate: int = 16000,
    source_channels: int = 2,
    source_dtype: str = "int32",
) -> bytes:
    """Convert hardware audio (e.g. 48kHz stereo int32 from INMP441) to Gemini format (16kHz mono int16)."""
    if not data:
        return b""

    dtype_lower = source_dtype.lower().strip()
    if dtype_lower in ("int32", "s32", "s32_le"):
        raw = np.frombuffer(data, dtype=np.int32)
        # Shift upper 16 bits down (INMP441 sends 24-bit MSB aligned in 32-bit slot)
        samples = (raw >> 16).astype(np.int16)
    elif dtype_lower in ("float32", "f32"):
        raw = np.frombuffer(data, dtype=np.float32)
        samples = np.clip(raw * 32767.0, -32768, 32767).astype(np.int16)
    else:
        samples = np.frombuffer(data, dtype=np.int16)

    if len(samples) == 0:
        return b""

    # Downmix channels to mono
    if source_channels > 1:
        frame_count = len(samples) // source_channels
        if frame_count == 0:
            return b""
        frames = samples[: frame_count * source_channels].reshape(frame_count, source_channels)
        # Average left and right microphones
        mono_samples = (frames.astype(np.int32).sum(axis=1) // source_channels).astype(np.int16)
    else:
        mono_samples = samples

    # Resample to target rate (e.g. 48000 -> 16000)
    return resample_pcm16(mono_samples.tobytes(), source_rate, target_rate, channels=1)

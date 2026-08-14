"""Small, dependency-free PCM resampler for audio device boundary rates."""

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
    # worst voice-band aliasing when a 48 kHz WASAPI mic is reduced to 16 kHz.
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

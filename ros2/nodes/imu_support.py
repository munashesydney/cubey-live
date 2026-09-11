"""ROS-independent IMU transport, quaternion math, and measured scan translation."""
from bisect import bisect_left
from collections import deque
from dataclasses import dataclass
import math
import threading

import numpy as np


def wrap(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


def quaternion_multiply(a, b):
    """Hamilton product, in ROS (x, y, z, w) order."""
    x, y, z, w = a
    i, j, k, r = b
    return (w*i+x*r+y*k-z*j, w*j-x*k+y*r+z*i,
            w*k+x*j-y*i+z*r, w*r-x*i-y*j-z*k)


def conjugate(q):
    return (-q[0], -q[1], -q[2], q[3])


def yaw(q):
    x, y, z, w = q
    return math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))


def quaternion_from_euler(roll, pitch, heading):
    cr, sr = math.cos(roll/2), math.sin(roll/2)
    cp, sp = math.cos(pitch/2), math.sin(pitch/2)
    cy, sy = math.cos(heading/2), math.sin(heading/2)
    return (sr*cp*cy-cr*sp*sy, cr*sp*cy+sr*cp*sy,
            cr*cp*sy-sr*sp*cy, cr*cp*cy+sr*sp*sy)


@dataclass(frozen=True)
class ImuSample:
    stamp: float
    quaternion: tuple
    stream: tuple
    calibration: int


class ImuPacketClock:
    """Reject repeated/late packets, and map the SH2 clock to ROS time.

    The minimum observed receipt offset estimates transport latency. A backlog
    therefore keeps its old timestamps instead of masquerading as fresh data.
    A different boot/epoch is observable by consumers, even with identical yaw.
    """
    def __init__(self, max_age=0.2):
        self.max_age = max_age
        self.stream = None
        self.sequence = None
        self.sensor_us = None
        self.offset = None
        self.last_stamp = None
        self.reason = "Waiting for timestamped IMU firmware"
        self.host_clock_generation = 0
        self.host_clock_offset = None

    def parse(self, line, received_at, received_monotonic=None):
        if not line.startswith("IMU:"):
            return None
        if received_monotonic is not None:
            host_offset = received_at-received_monotonic
            if self.host_clock_offset is not None:
                jump = host_offset-self.host_clock_offset
                if abs(jump) > 0.1:
                    # NTP can step wall time after boot. Preserve measured
                    # transport delay instead of treating that step as backlog.
                    if self.offset is not None:
                        self.offset += jump
                    if self.last_stamp is not None:
                        self.last_stamp += jump
                    self.host_clock_generation += 1
            self.host_clock_offset = host_offset
        try:
            kv = dict(part.split("=", 1) for part in line[4:].split(","))
            if kv.get("ok") != "1":
                raise ValueError("IMU reports unhealthy")
            stream = (int(kv["boot"]), int(kv["epoch"]))
            seq, sensor_us = int(kv["seq"]), int(kv["t_us"])
            age = int(kv["age_ms"])/1000.0
            q = tuple(float(kv[key]) for key in ("qx", "qy", "qz", "qw"))
            norm = math.sqrt(sum(v*v for v in q))
            if not math.isfinite(norm) or not 0.8 <= norm <= 1.2:
                raise ValueError("Invalid IMU quaternion")
            if sensor_us < 0 or not 0 <= age <= self.max_age:
                raise ValueError("Stale IMU sample")
            if stream != self.stream:
                self.stream, self.sequence, self.sensor_us = stream, None, None
                self.offset, self.last_stamp = None, None
            if self.sequence is not None:
                sequence_delta = (seq-self.sequence) & 0xffffffff
                if sequence_delta == 0 or sequence_delta >= 0x80000000:
                    return None
                if sensor_us <= self.sensor_us:
                    raise ValueError("IMU clock regressed")
            candidate_offset = received_at-age-sensor_us/1e6
            self.offset = candidate_offset if self.offset is None else min(self.offset, candidate_offset)
            stamp = sensor_us/1e6+self.offset
            self.sequence, self.sensor_us = seq, sensor_us
            if received_at-stamp > self.max_age or stamp > received_at+0.01:
                raise ValueError("IMU serial backlog")
            if self.last_stamp is not None and stamp <= self.last_stamp:
                return None
            self.last_stamp = stamp
            self.reason = ""
            return ImuSample(stamp, tuple(v/norm for v in q), stream, int(kv["cal"]))
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            self.reason = str(error)
            return None


class HeadingHistory:
    def __init__(self):
        self.samples = deque(maxlen=200)
        self._lock = threading.Lock()

    def add(self, stamp, heading):
        with self._lock:
            if self.samples and stamp <= self.samples[-1][0]:
                return False
            self.samples.append((stamp, heading))
            return True

    def at(self, stamp, tolerance=0.06):
        return self.at_many([stamp], tolerance)[0]

    def at_many(self, stamps, tolerance=0.06):
        """Interpolate many ordered beam times from one thread-safe snapshot.

        A LiDAR revolution contains roughly 500 beams. Copying and linearly
        searching the entire history for every beam consumed most of a Pi CPU
        core and delayed Nav2 transforms. One snapshot plus binary searches is
        bounded and produces the same measured-heading result.
        """
        with self._lock:
            samples = list(self.samples)
        if not samples:
            return [None] * len(stamps)

        times = [sample[0] for sample in samples]
        results = []
        for stamp in stamps:
            if stamp < times[0]-tolerance or stamp > times[-1]+tolerance:
                results.append(None)
                continue
            index = bisect_left(times, stamp)
            if index < len(samples) and times[index] == stamp:
                results.append(samples[index][1])
                continue
            if 0 < index < len(samples):
                ta, a = samples[index-1]
                tb, b = samples[index]
                if tb-ta <= 0.12:
                    results.append(wrap(a + wrap(b-a)*(stamp-ta)/(tb-ta)))
                else:
                    results.append(None)
                continue
            nearest = samples[0] if index == 0 else samples[-1]
            results.append(nearest[1] if abs(nearest[0]-stamp) <= tolerance else None)
        return results


def heading_jump_metrics(samples, stamp, heading):
    """Bound both one-sample change and sustained turn rate with timing tolerance.

    The 0.025 rad angle allowance absorbs approximately one report's timing
    jitter; it is applied once across the window, not once per sample.
    """
    previous_stamp, previous = samples[-1]
    dt = stamp-previous_stamp
    delta = wrap(heading-previous)
    window_stamp, window_heading = samples[0]
    for candidate_stamp, candidate_heading in samples:
        if candidate_stamp <= stamp-0.08:
            window_stamp, window_heading = candidate_stamp, candidate_heading
        else:
            break
    window_dt = stamp-window_stamp
    window_delta = wrap(heading-window_heading)
    allowance = 0.025
    return {"pair_rate_rad_s": delta/dt, "window_rate_rad_s": window_delta/window_dt,
            "window_interval_s": window_dt, "angle_allowance_rad": allowance,
            "jump": abs(delta) > 4.0*dt+allowance or abs(window_delta) > 4.0*window_dt+allowance}


def match_translation(previous, current, rotation, max_translation=0.2):
    """Robust point-to-plane translation with rotation supplied by the IMU.

    Points are ordered by beam angle and expressed about the base origin.
    Reject weak overlap and geometrically unobservable translation (e.g. a
    single wall). Returns (dx, dy, residual_variance) or None; no command input.
    """
    if len(previous) < 20 or len(current) < 20:
        return None
    tangent = previous[2:]-previous[:-2]
    lengths = np.linalg.norm(tangent, axis=1)
    valid = (lengths > 0.015) & (lengths < 0.8)
    reference = previous[1:-1][valid]
    tangent = tangent[valid]
    if len(reference) < 15:
        return None
    normals = np.column_stack((-tangent[:, 1], tangent[:, 0])) / lengths[valid, None]
    c, s = math.cos(rotation), math.sin(rotation)
    rotated = current @ np.array([[c, s], [-s, c]])
    translation = np.zeros(2)
    for _ in range(10):
        moved = rotated+translation
        distances = np.sum((moved[:, None, :]-reference[None, :, :])**2, axis=2)
        nearest = np.argmin(distances, axis=1)
        keep = distances[np.arange(len(moved)), nearest] < 0.25**2
        if np.count_nonzero(keep) < max(15, len(moved)*0.55):
            return None
        n = normals[nearest[keep]]
        residual = np.sum(n*(reference[nearest[keep]]-moved[keep]), axis=1)
        weights = np.minimum(1.0, 0.025/np.maximum(np.abs(residual), 1e-9))
        hessian = n.T @ (weights[:, None]*n)
        eigenvalues = np.linalg.eigvalsh(hessian)
        if eigenvalues[0] < 0.025*eigenvalues[-1]:
            return None
        step = np.linalg.solve(hessian, n.T @ (weights*residual))
        translation += step
        if np.linalg.norm(translation) > max_translation:
            return None
        if np.linalg.norm(step) < 0.0002:
            break
    residual = residual-n@step
    variance = float(np.mean(np.minimum(residual**2, 0.25**2)))
    if variance > 0.06**2:
        return None
    # Conservative floor includes timing, IMU rotation and shared scan errors.
    return float(translation[0]), float(translation[1]), max(0.0001, variance)

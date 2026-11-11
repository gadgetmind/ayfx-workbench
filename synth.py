"""Single-channel AY audition renderer based on Remy Sharp's zx-tools."""

from array import array
from sys import byteorder

from ayfx import Frame

AY_CLOCK = 1_773_400
SAMPLE_RATE = 44_100
FRAME_RATE = 50
VOLUME = tuple(value // 3 for value in (
    0, 836, 1212, 1773, 2619, 3875, 5397, 8823,
    10392, 16706, 23339, 29292, 36969, 46421, 55195, 65535,
))


def render_pcm(frames: tuple[Frame, ...]) -> bytes:
    """Render signed, little-endian, 16-bit mono PCM."""
    tone_count = tone_state = noise_count = noise_state = divider = 0
    noise_register = 0xFFFF
    tick_phase = 0
    tick_denominator = 8 * SAMPLE_RATE
    previous = filtered = 0.0
    pcm = array("h")
    tail = (Frame(False, False, 0, 0, 0),) * 2 if frames else ()
    for frame in (*frames, *tail):
        level = VOLUME[frame.volume]
        for _ in range(SAMPLE_RATE // FRAME_RATE):
            ticks, tick_phase = divmod(tick_phase + AY_CLOCK, tick_denominator)
            mixed = 0
            for _ in range(ticks):
                if tone_count >= frame.tone:
                    tone_count = 0
                    tone_state ^= 1
                tone_count += 1
                divider ^= 1
                if divider:
                    if noise_count == 0:
                        feedback = noise_state ^ ((noise_register >> 13) & 1) ^ 1
                        noise_state = (noise_register >> 15) & 1
                        noise_register = ((noise_register << 1) | feedback) & 0xFFFF
                    noise_count = (noise_count + 1) & 31
                    if noise_count >= frame.noise:
                        noise_count = 0
                if (tone_state or not frame.tone_on) and (noise_state or not frame.noise_on):
                    mixed += level
            sample = mixed / ticks
            # Remove the PSG's DC component before sending PCM to the device.
            filtered = sample - previous + 0.995 * filtered
            previous = sample
            pcm.append(int(filtered))
    if byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()

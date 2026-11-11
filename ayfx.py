"""Shiru AYFX bank I/O and frame decoding."""

import json
from dataclasses import dataclass
from struct import pack, unpack_from

END = b"\xd0\x20"
MAX_EFFECTS = 256


class BankError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    tone_on: bool
    noise_on: bool
    tone: int
    noise: int
    volume: int


@dataclass(frozen=True)
class Effect:
    stream: bytes
    frames: tuple[Frame, ...]
    name: bytes = b""

    @property
    def title(self) -> str:
        try:
            return self.name.decode("utf-8")
        except UnicodeDecodeError:
            return self.name.decode("latin-1")

    @property
    def name_bytes(self) -> int:
        return len(self.name) + bool(self.name)

    @property
    def bank_bytes(self) -> int:
        """Contribution to a saved bank, including the two-byte offset."""
        return 2 + len(self.stream) + self.name_bytes

    def renamed(self, title: str) -> "Effect":
        return Effect(self.stream, self.frames, title.encode("utf-8"))


def effect_from_frames(frames: tuple[Frame, ...], name: bytes = b"") -> Effect:
    stream = bytearray()
    tone = noise = 0
    for frame in frames:
        flags = frame.volume
        if not frame.tone_on:
            flags |= 0x10
        if not frame.noise_on:
            flags |= 0x80
        values = bytearray()
        if frame.tone != tone:
            tone = frame.tone
            flags |= 0x20
            values.extend(pack("<H", tone))
        if frame.noise != noise:
            noise = frame.noise
            flags |= 0x40
            values.append(noise)
        stream.append(flags)
        stream.extend(values)
    return Effect(bytes(stream) + END, frames, name)


def nexy_name(field: bytes) -> bytes | None:
    """Return the title if the field ends with a recognised NexySFX block."""
    title, marker, metadata = field.partition(b"NEXY{")
    if not marker:
        return None
    try:
        attributes = json.loads(b"{" + metadata)
    except (ValueError, RecursionError):
        return None
    if not isinstance(attributes, dict) or not isinstance(attributes.get("name"), str):
        return None
    return title.rstrip() or attributes["name"].replace("\0", "").encode("utf-8", errors="replace")


def decode_effect(data: bytes) -> Effect:
    tone = noise = offset = 0
    frames = []
    while offset < len(data):
        flags = data[offset]
        offset += 1
        if flags & 0x20:
            if offset + 2 > len(data):
                break
            tone = unpack_from("<H", data, offset)[0] & 0xFFF
            offset += 2
        if flags & 0x40:
            if offset == len(data):
                break
            noise_byte = data[offset]
            offset += 1
            if flags == 0xD0 and noise_byte >= 0x20:
                field = data[offset:].partition(b"\0")[0]
                if noise_byte == ord("N") and field.startswith(b"EXY{"):
                    # This variant places NEXY directly after D0 instead of D0 20.
                    title = nexy_name(b"N" + field)
                    if title is not None:
                        return Effect(data[:offset - 2] + END, tuple(frames), title)
                title = nexy_name(field)
                return Effect(data[:offset], tuple(frames), field if title is None else title)
            noise = noise_byte & 31
        frames.append(Frame(not flags & 0x10, not flags & 0x80, tone, noise, flags & 15))
    raise BankError("Truncated effect or missing AYFX end marker.")


def decode_bank(data: bytes) -> list[Effect]:
    if not data:
        raise BankError("The file is empty.")
    count = data[0] or MAX_EFFECTS
    header_end = 1 + count * 2
    if len(data) < header_end:
        raise BankError("The bank's offset table is incomplete.")
    offsets = [
        position + 1 + unpack_from("<H", data, position)[0]
        for position in range(1, header_end, 2)
    ]
    unique = sorted(set(offsets))
    if unique[0] < header_end or unique[-1] >= len(data):
        raise BankError("An effect offset points outside the bank data.")
    effects = {
        start: decode_effect(data[start:end])
        for start, end in zip(unique, unique[1:] + [len(data)])
    }
    return [effects[offset] for offset in offsets]


def encode_bank(effects: list[Effect]) -> bytes:
    if not 1 <= len(effects) <= MAX_EFFECTS:
        raise BankError("A standard AFB bank must contain 1 to 256 effects.")
    header = bytearray([len(effects) & 255])
    position = 1 + 2 * len(effects)
    body = bytearray()
    for effect in effects:
        relative = position - len(header) - 1
        if relative > 0xFFFF:
            raise BankError("The bank is too large for its 16-bit effect offsets.")
        header.extend(pack("<H", relative))
        body.extend(effect.stream)
        if effect.name:
            body.extend(effect.name + b"\0")
        position = 1 + 2 * len(effects) + len(body)
    return bytes(header + body)

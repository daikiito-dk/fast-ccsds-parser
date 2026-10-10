"""Shared test helpers.

``make_packet_bytes`` builds complete space packets from raw header
fields; the bit layout mirrors CCSDS 133.0-B-2 (see the
``fast_ccsds_parser.header`` module docstring for the field layout).
"""

import struct

import pytest

#: Maximum legal value per raw header field (CCSDS 133.0-B-2 bit widths).
_FIELD_MAX = {
    "version": 0b111,
    "packet_type": 0b1,
    "secondary_header_flag": 0b1,
    "apid": 0x7FF,
    "sequence_flags": 0b11,
    "sequence_count": 0x3FFF,
}


def make_packet_bytes(
    *,
    apid: int = 0x123,
    sequence_count: int = 0,
    version: int = 0,
    packet_type: int = 0,
    secondary_header_flag: int = 0,
    sequence_flags: int = 0b11,
    payload: bytes = b"\x00",
) -> bytes:
    """Build one complete space packet: primary header + data field.

    ``packet_length`` is derived automatically from ``len(payload) - 1``
    (CCSDS stores the data field length minus one, so the data field is
    always at least one byte).
    """
    if len(payload) < 1:
        raise ValueError("the CCSDS data field must be at least 1 byte")
    fields = {
        "version": version,
        "packet_type": packet_type,
        "secondary_header_flag": secondary_header_flag,
        "apid": apid,
        "sequence_flags": sequence_flags,
        "sequence_count": sequence_count,
    }
    for name, value in fields.items():
        if not 0 <= value <= _FIELD_MAX[name]:
            # Guard against silent bit bleed-through into neighbouring
            # header fields (e.g. an APID above 0x7FF flips the secondary
            # header flag).
            raise ValueError(f"{name}={value} is outside 0..{_FIELD_MAX[name]}")
    word1 = (
        (version << 13)
        | (packet_type << 12)
        | (secondary_header_flag << 11)
        | apid
    )
    word2 = (sequence_flags << 14) | sequence_count
    word3 = len(payload) - 1
    return struct.pack(">HHH", word1, word2, word3) + payload


@pytest.fixture
def build_packet():
    """Keyword-only access to :func:`make_packet_bytes` inside tests."""
    return make_packet_bytes

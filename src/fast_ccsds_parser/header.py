"""Parsing of the 6-byte CCSDS space packet primary header.

The primary header is defined in section 4.1.2 of the CCSDS Space Packet
Protocol standard (CCSDS 133.0-B-2), and is laid out as follows (all fields
big-endian, 48 bits total)::

    bit:   0   1   2   3   4   5   6   7   8   9  10  11  12  13  14  15
           +---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+
    0-1    | version  | T | S |               APID                       |
           +---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+
    2-3    | seq flags |        sequence count / packet name             |
           +---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+
    4-5    |                     packet length                          |
           +---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+

* ``version``: 3 bits, ``0b000`` for space packets.
* ``T`` (type): 1 bit, 0 = telemetry, 1 = telecommand.
* ``S`` (secondary header flag): 1 bit, 1 = a secondary header follows.
* ``APID``: 11 bits, application process identifier (all ones = idle packet).
* ``seq flags``: 2 bits, segmentation status of the packet.
* ``sequence count``: 14 bits, packet sequence count or packet name.
* ``packet length``: 16 bits, length of the data field **minus one**.
"""

from __future__ import annotations

import struct
from enum import IntEnum
from typing import NamedTuple

from .exceptions import InvalidPacketError, TruncatedPacketError

__all__ = [
    "CCSDS_PACKET_VERSION",
    "IDLE_APID",
    "PRIMARY_HEADER_SIZE",
    "PacketType",
    "SequenceFlags",
    "PrimaryHeader",
    "parse_primary_header",
]

#: Packet version number defined for CCSDS space packets (CCSDS 133.0-B-2).
CCSDS_PACKET_VERSION = 0

#: Size of the primary header in bytes.
PRIMARY_HEADER_SIZE = 6

#: Reserved all-ones APID that marks idle packets.
IDLE_APID = 0x7FF

#: Three 16-bit words of the primary header, big-endian.
_HEADER = struct.Struct(">HHH")

# Bit masks inside each 16-bit word of the header.
_VERSION_MASK = 0xE000
_TYPE_MASK = 0x1000
_SECONDARY_HEADER_FLAG_MASK = 0x0800
_APID_MASK = 0x07FF
_SEQUENCE_FLAGS_MASK = 0xC000
_SEQUENCE_COUNT_MASK = 0x3FFF


class PacketType(IntEnum):
    """Values of the ``type`` bit in the packet identification word."""

    TELEMETRY = 0
    TELECOMMAND = 1


class SequenceFlags(IntEnum):
    """Values of the 2-bit sequence field (segmentation status)."""

    CONTINUATION = 0b00
    FIRST_SEGMENT = 0b01
    LAST_SEGMENT = 0b10
    UNSEGMENTED = 0b11


class PrimaryHeader(NamedTuple):
    """One parsed CCSDS space packet primary header.

    Instances behave like a plain tuple of seven ints
    ``(version, type, secondary_header_flag, apid, sequence_flags,
    sequence_count, packet_length)`` and also support named-field access,
    e.g. ``header.apid`` or ``header.total_length``.
    """

    version: int
    type: int
    secondary_header_flag: int
    apid: int
    sequence_flags: int
    sequence_count: int
    packet_length: int

    # -- derived values -------------------------------------------------

    @property
    def has_secondary_header(self) -> bool:
        """True if a secondary header follows the primary header."""
        return bool(self.secondary_header_flag)

    @property
    def is_idle(self) -> bool:
        """True if this is an idle packet (APID = all ones)."""
        return self.apid == IDLE_APID

    @property
    def data_field_length(self) -> int:
        """Number of bytes in the data field (``packet_length + 1``).

        Per CCSDS 133.0-B-2 the ``packet_length`` header field stores the
        data field length minus one, so the data field is always at least
        one byte long.
        """
        return self.packet_length + 1

    @property
    def total_length(self) -> int:
        """Total packet size in bytes, including the primary header."""
        return PRIMARY_HEADER_SIZE + self.packet_length + 1

    # -- conversions ------------------------------------------------------

    def to_dict(self) -> dict[str, int]:
        """Return the seven header fields as a plain :class:`dict`."""
        return {
            "version": self.version,
            "type": self.type,
            "secondary_header_flag": self.secondary_header_flag,
            "apid": self.apid,
            "sequence_flags": self.sequence_flags,
            "sequence_count": self.sequence_count,
            "packet_length": self.packet_length,
        }

    # -- parsing ----------------------------------------------------------

    @classmethod
    def from_bytes(
        cls,
        data: bytes | bytearray | memoryview,
        *,
        offset: int = 0,
        strict: bool = True,
    ) -> PrimaryHeader:
        """Parse a primary header from ``data`` starting at ``offset``.

        Parameters
        ----------
        data:
            Any bytes-like object holding at least ``PRIMARY_HEADER_SIZE``
            bytes after ``offset`` (``bytes``, ``bytearray``, ``memoryview``).
        offset:
            Byte position at which the header starts.
        strict:
            If True (default), raise :class:`InvalidPacketError` when the
            packet version is not ``0``. Non-space-packet data is almost
            always a framing bug, so strict is the sensible default; pass
            ``strict=False`` to parse the header anyway.

        Returns
        -------
        PrimaryHeader
        """
        available = len(data) - offset
        if available < PRIMARY_HEADER_SIZE:
            raise TruncatedPacketError(
                f"need {PRIMARY_HEADER_SIZE} bytes for the primary header at "
                f"offset {offset}, but only {available} byte(s) are available"
            )
        word1, word2, word3 = _HEADER.unpack_from(data, offset)
        header = cls(
            version=(word1 & _VERSION_MASK) >> 13,
            type=(word1 & _TYPE_MASK) >> 12,
            secondary_header_flag=(word1 & _SECONDARY_HEADER_FLAG_MASK) >> 11,
            apid=word1 & _APID_MASK,
            sequence_flags=(word2 & _SEQUENCE_FLAGS_MASK) >> 14,
            sequence_count=word2 & _SEQUENCE_COUNT_MASK,
            packet_length=word3,
        )
        if strict and header.version != CCSDS_PACKET_VERSION:
            raise InvalidPacketError(
                f"packet version is {header.version}, expected "
                f"{CCSDS_PACKET_VERSION} for a CCSDS 133.0-B-2 space packet "
                "(pass strict=False to parse anyway)"
            )
        return header


def parse_primary_header(
    data: bytes | bytearray | memoryview,
    *,
    offset: int = 0,
    strict: bool = True,
) -> dict[str, int]:
    """Parse a primary header and return the fields as a :class:`dict`.

    Shortcut for ``PrimaryHeader.from_bytes(...).to_dict()``. See
    :meth:`PrimaryHeader.from_bytes` for the parameters.
    """
    return PrimaryHeader.from_bytes(data, offset=offset, strict=strict).to_dict()

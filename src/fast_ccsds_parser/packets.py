"""Splitting byte streams into CCSDS space packets with their payloads.

Two entry styles are provided:

* In-memory buffers (``bytes``, ``bytearray``, ``memoryview``):
  :func:`iter_packets` / :func:`parse_packets`, plus :func:`parse_packet`
  for a single packet.
* Large dump files, streamed packet-by-packet with O(1) memory:
  :func:`iter_file` / :func:`parse_file`.

Payload semantics
-----------------
Per CCSDS 133.0-B-2, the data field = optional secondary header + user
data. :attr:`SpacePacket.payload` is the *full* data field; check
``header.has_secondary_header`` to know whether it begins with a
secondary header. Decoding mission-specific secondary-header layouts is
out of scope for this structural parser (planned for a later phase).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO

from .exceptions import InvalidPacketError, TruncatedPacketError
from .header import PRIMARY_HEADER_SIZE, PrimaryHeader

__all__ = [
    "SpacePacket",
    "parse_packet",
    "iter_packets",
    "parse_packets",
    "iter_file",
    "parse_file",
]


@dataclass(frozen=True, slots=True)
class SpacePacket:
    """One parsed CCSDS space packet.

    Attributes
    ----------
    header:
        The parsed :class:`~fast_ccsds_parser.PrimaryHeader`.
    payload:
        The packet data field: secondary header (if present) + user data.
    raw:
        The complete packet bytes, header included.
    """

    header: PrimaryHeader
    payload: bytes
    raw: bytes

    def to_dict(self) -> dict[str, int | bytes]:
        """Flatten into a :class:`dict` of header fields plus ``payload``.

        The seven header keys come first (see
        :meth:`PrimaryHeader.to_dict`), followed by ``payload``. This shape
        is intended to be trivially feedable into ``pandas.DataFrame``.
        """
        result: dict[str, int | bytes] = self.header.to_dict()
        result["payload"] = self.payload
        return result


def parse_packet(
    data: bytes | bytearray | memoryview,
    *,
    strict: bool = True,
) -> SpacePacket:
    """Parse exactly one space packet from ``data``.

    Parameters
    ----------
    data:
        The full packet bytes: primary header + data field.
    strict:
        Reject packets whose version is not 0 (see
        :meth:`PrimaryHeader.from_bytes`).

    Raises
    ------
    InvalidPacketError
        If bytes remain behind the packet (``data`` must hold exactly one
        packet), or the version check fails in strict mode.
    TruncatedPacketError
        If ``data`` is shorter than the packet declares.
    """
    header = PrimaryHeader.from_bytes(data, strict=strict)
    size = len(data)
    if size < header.total_length:
        raise TruncatedPacketError(
            f"packet (APID {header.apid}) declares {header.total_length} bytes "
            f"in total, but only {size} were given"
        )
    if size > header.total_length:
        raise InvalidPacketError(
            f"expected exactly {header.total_length} bytes for one packet, "
            f"got {size} ({size - header.total_length} trailing byte(s)); "
            "use iter_packets() to parse multiple concatenated packets"
        )
    return SpacePacket(
        header=header,
        payload=bytes(data[PRIMARY_HEADER_SIZE : header.total_length]),
        raw=bytes(data[: header.total_length]),
    )


def iter_packets(
    data: bytes | bytearray | memoryview,
    *,
    strict: bool = True,
    skip_idle: bool = False,
) -> Iterator[SpacePacket]:
    """Iterate over space packets concatenated back-to-back in ``data``.

    A single pass over the buffer; no intermediate objects beyond the
    yielded packets. Works comfortably on multi-megabyte telemetry dumps.

    Parameters
    ----------
    data:
        Buffer containing whole packets (``bytes``, ``bytearray``,
        ``memoryview``).
    strict:
        Reject packets whose version is not 0.
    skip_idle:
        Skip idle packets (APID = all ones) instead of yielding them.

    Raises
    ------
    InvalidPacketError
        If a packet fails the strict version check.
    TruncatedPacketError
        If the buffer ends with a partial header or a partial data field.

    Yields
    ------
    SpacePacket
    """
    size = len(data)
    offset = 0
    while offset < size:
        header = PrimaryHeader.from_bytes(data, offset=offset, strict=strict)
        end = offset + header.total_length
        if end > size:
            raise TruncatedPacketError(
                f"packet at offset {offset} (APID {header.apid}) declares "
                f"{header.total_length} bytes, but only {size - offset} "
                "remain in the buffer"
            )
        if not (skip_idle and header.is_idle):
            yield SpacePacket(
                header=header,
                payload=bytes(data[offset + PRIMARY_HEADER_SIZE : end]),
                raw=bytes(data[offset:end]),
            )
        offset = end


def parse_packets(
    data: bytes | bytearray | memoryview,
    *,
    strict: bool = True,
    skip_idle: bool = False,
) -> list[SpacePacket]:
    """Like :func:`iter_packets` but returns a :class:`list`."""
    return list(iter_packets(data, strict=strict, skip_idle=skip_idle))


def iter_file(
    path: str | os.PathLike[str],
    *,
    strict: bool = True,
    skip_idle: bool = False,
) -> Iterator[SpacePacket]:
    """Stream space packets from a file, one at a time.

    Reads each packet with buffered I/O as it is yielded, so memory usage
    stays constant regardless of file size (gigabyte-scale ground-station
    dumps are fine). For files you can also pass a file object.

    Parameters
    ----------
    path:
        Path to a file of concatenated space packets.
    strict:
        Reject packets whose version is not 0.
    skip_idle:
        Skip idle packets (APID = all ones) instead of yielding them.

    Raises
    ------
    InvalidPacketError
        If a packet fails the strict version check.
    TruncatedPacketError
        If the file ends in the middle of a header or data field.

    Yields
    ------
    SpacePacket
    """
    with open(path, "rb") as file:
        yield from _iter_file_object(file, strict=strict, skip_idle=skip_idle)


def parse_file(
    path: str | os.PathLike[str],
    *,
    strict: bool = True,
    skip_idle: bool = False,
) -> list[SpacePacket]:
    """Like :func:`iter_file` but returns a :class:`list`."""
    return list(iter_file(path, strict=strict, skip_idle=skip_idle))


def _iter_file_object(
    file: BinaryIO,
    *,
    strict: bool = True,
    skip_idle: bool = False,
) -> Iterator[SpacePacket]:
    """Shared implementation for reading packets from a binary file object."""
    while True:
        header_bytes = file.read(PRIMARY_HEADER_SIZE)
        if not header_bytes:
            return  # clean EOF
        header = PrimaryHeader.from_bytes(header_bytes, strict=strict)
        data_field = file.read(header.data_field_length)
        if len(data_field) < header.data_field_length:
            raise TruncatedPacketError(
                f"file ended mid-packet: packet (APID {header.apid}) declares "
                f"a {header.data_field_length}-byte data field, but only "
                f"{len(data_field)} byte(s) were read"
            )
        if not (skip_idle and header.is_idle):
            yield SpacePacket(
                header=header,
                payload=data_field,
                raw=header_bytes + data_field,
            )

"""Decoding CCSDS time codes (CUC and CDS), per CCSDS 301.0-B-4.

Telemetry packets conventionally start their secondary header (the start
of the data field) with a time code. This module decodes the two binary
time codes whose P-field (preamble) describes their own format:

* **CUC** (unsegmented): a binary counter of seconds since an epoch
  ("coarse time") plus a binary fraction of a second ("fine time").
* **CDS** (day segmented): days since an epoch, milliseconds of day, and
  an optional sub-millisecond segment.

:func:`parse_timecode` reads the P-field and dispatches to
:func:`parse_cuc` or :func:`parse_cds`. All three return a
:class:`TimeCode`; its ``timestamp`` is a timezone-aware
:class:`~datetime.datetime`. ``TimeCode.n_bytes`` tells you how many bytes
the code occupied, so you can locate whatever follows it inside a
secondary header.

Time scales and leap seconds
----------------------------
Per the standard, level 1 CUC counts seconds on the **TAI** scale from
1958-01-01: the count keeps increasing through UTC leap seconds. The
decoded ``datetime`` labels that count as UTC, so for a fully
spec-compliant mission it reads ahead of UTC by the accumulated leap
seconds (37 s since 2017-01-01). Many missions instead fill CUC with a
naive UTC count, in which case the result is already UTC. Apply your
mission's convention (for example, subtract the TAI-UTC offset)
accordingly; this module deliberately does not guess.

CDS is UTC-based: days and milliseconds map directly onto civil days, so
the arithmetic conversion used here matches the standard (on a day with
a leap second, milliseconds-of-day may reach 86,400,999 per annex A).

Fractional seconds below one microsecond (CUC with three fine octets has
2**-24 s resolution; the 32-bit CDS sub-millisecond segment carries
picoseconds) are truncated toward zero, because ``datetime`` carries
microsecond resolution.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from .exceptions import (
    InvalidPacketError,
    TruncatedPacketError,
    UnsupportedTimeCodeError,
)

__all__ = [
    "CCSDS_EPOCH",
    "TimeCode",
    "parse_cuc",
    "parse_cds",
    "parse_timecode",
]

#: The CCSDS recommended epoch: 1958 January 1, 00:00:00.
CCSDS_EPOCH = datetime(1958, 1, 1, tzinfo=timezone.utc)

# Time code identifications (P-field bits 1-3). 0b000, 0b011 and 0b111 are
# reserved, 0b101 is CCS, 0b110 is an agency-defined level 3/4 code.
_ID_CUC_LEVEL_1 = 0b001
_ID_CUC_LEVEL_2 = 0b010
_ID_CDS = 0b100

# CDS milliseconds-of-day may pass 86,399,999 on days with leap-second
# adjustments (CCSDS 301.0-B-4 annex A).
_MAX_MS_OF_DAY = 86_400_999

_CDS_SUBMS_OCTETS = {0b00: 0, 0b01: 2, 0b10: 4}
#: Maximum value of the CDS sub-millisecond segment: microseconds (0-999)
#: for the 16-bit segment, picoseconds (0-999,999,999) for the 32-bit one.
_CDS_SUBMS_MAX = {2: 999, 4: 999_999_999}


class TimeCode(NamedTuple):
    """A decoded CCSDS time code.

    Attributes
    ----------
    timestamp:
        The decoded instant as a timezone-aware
        :class:`~datetime.datetime`. See the module docstring for the
        time-scale caveats (TAI vs UTC).
    format:
        ``"cuc"`` or ``"cds"``.
    level:
        1 (CCSDS 1958 epoch) or 2 (agency-defined epoch).
    preamble:
        The first P-field octet, as an integer.
    n_bytes:
        Total bytes consumed, P-field included. Use it to locate whatever
        follows the time code inside a secondary header.
    """

    timestamp: datetime
    format: str
    level: int
    preamble: int
    n_bytes: int


def parse_cuc(
    data: bytes | bytearray | memoryview,
    *,
    offset: int = 0,
    epoch: datetime | None = None,
) -> TimeCode:
    """Decode a CUC (unsegmented) time code at ``offset`` in ``data``.

    Parameters
    ----------
    data:
        Buffer holding the time code (a whole payload works).
    offset:
        Where the P-field starts inside ``data``.
    epoch:
        Epoch for level 2 (agency-defined) codes. Naive datetimes are
        assumed UTC. Optional for level 1 codes, where the CCSDS epoch
        (1958-01-01) is the default.

    Raises
    ------
    UnsupportedTimeCodeError
        If the P-field does not identify a CUC code, if a level 2 code
        arrives without ``epoch``, or if a P-field extension beyond the
        two defined octets is used.
    TruncatedPacketError
        If fewer bytes remain than the P-field declares.
    InvalidPacketError
        If the decoded time falls outside the ``datetime`` range.
    """
    available = _available(data, offset, "CUC")
    preamble = data[offset]
    time_code_id = (preamble >> 4) & 0b111
    if time_code_id not in (_ID_CUC_LEVEL_1, _ID_CUC_LEVEL_2):
        raise UnsupportedTimeCodeError(
            f"not a CUC time code: P-field time code identification "
            f"{time_code_id:03b} (0b001/0b010 expected)"
        )
    coarse_octets = ((preamble >> 2) & 0b11) + 1
    fine_octets = preamble & 0b11
    n_preamble = 1
    if preamble & 0x80:  # extension flag: a second P-field octet follows
        if available < 2:
            raise TruncatedPacketError(
                "CUC P-field signals a second octet, but it is missing"
            )
        second = data[offset + 1]
        if second & 0x80:
            raise UnsupportedTimeCodeError(
                "CUC P-fields longer than two octets are not defined by "
                "CCSDS 301.0-B-4"
            )
        coarse_octets += (second >> 5) & 0b11
        fine_octets += (second >> 2) & 0b111
        n_preamble = 2
    n_tfield = coarse_octets + fine_octets
    _require(available, n_preamble + n_tfield, "CUC")

    start = offset + n_preamble
    coarse = int.from_bytes(data[start : start + coarse_octets], "big")
    fine = (
        int.from_bytes(data[start + coarse_octets : start + n_tfield], "big")
        if fine_octets
        else 0
    )
    microseconds = (fine * 1_000_000) >> (8 * fine_octets) if fine_octets else 0
    level = 1 if time_code_id == _ID_CUC_LEVEL_1 else 2
    base = _resolve_epoch(epoch, level)
    timestamp = _add(base, data[offset : start + n_tfield], seconds=coarse,
                     microseconds=microseconds)
    return TimeCode(timestamp, "cuc", level, preamble, n_preamble + n_tfield)


def parse_cds(
    data: bytes | bytearray | memoryview,
    *,
    offset: int = 0,
    epoch: datetime | None = None,
) -> TimeCode:
    """Decode a CDS (day segmented) time code at ``offset`` in ``data``.

    The T-field layout is ``day (16/24 bits) | milliseconds of day
    (32 bits) | sub-milliseconds (absent / 16-bit microseconds /
    32-bit picoseconds)``, as declared by the P-field. See
    :func:`parse_cuc` for the parameters and the errors; out-of-range
    segment values raise :class:`InvalidPacketError`.
    """
    available = _available(data, offset, "CDS")
    preamble = data[offset]
    time_code_id = (preamble >> 4) & 0b111
    if time_code_id != _ID_CDS:
        raise UnsupportedTimeCodeError(
            f"not a CDS time code: P-field time code identification "
            f"{time_code_id:03b} (0b100 expected)"
        )
    if preamble & 0x80:
        raise UnsupportedTimeCodeError(
            "CDS P-fields are a single octet, but the extension flag is set"
        )
    level = 2 if preamble & 0b1000 else 1
    day_octets = 3 if preamble & 0b100 else 2
    subms_code = preamble & 0b11
    if subms_code == 0b11:
        raise UnsupportedTimeCodeError(
            "CDS P-field uses the reserved sub-millisecond code 0b11"
        )
    subms_octets = _CDS_SUBMS_OCTETS[subms_code]
    n_tfield = day_octets + 4 + subms_octets
    _require(available, 1 + n_tfield, "CDS")

    start = offset + 1
    day = int.from_bytes(data[start : start + day_octets], "big")
    milliseconds = int.from_bytes(data[start + day_octets : start + day_octets + 4], "big")
    if milliseconds > _MAX_MS_OF_DAY:
        raise InvalidPacketError(
            f"CDS milliseconds-of-day {milliseconds} exceeds {_MAX_MS_OF_DAY}"
        )
    microseconds = 0
    if subms_octets:
        end = start + n_tfield
        subms = int.from_bytes(data[start + day_octets + 4 : end], "big")
        limit = _CDS_SUBMS_MAX[subms_octets]
        if subms > limit:
            raise InvalidPacketError(
                f"CDS sub-millisecond value {subms} exceeds {limit}"
            )
        # 16-bit segment: microseconds; 32-bit segment: picoseconds
        # (1 us = 10**6 ps), truncated to the microsecond resolution of
        # datetime.
        microseconds = subms if subms_octets == 2 else subms // 1_000_000
    base = _resolve_epoch(epoch, level)
    timestamp = _add(base, data[offset : start + n_tfield], days=day,
                     milliseconds=milliseconds, microseconds=microseconds)
    return TimeCode(timestamp, "cds", level, preamble, 1 + n_tfield)


def parse_timecode(
    data: bytes | bytearray | memoryview,
    *,
    offset: int = 0,
    epoch: datetime | None = None,
) -> TimeCode:
    """Decode the time code at ``offset``, dispatching on the P-field.

    CUC (identification 0b001/0b010) is decoded by :func:`parse_cuc`,
    CDS (0b100) by :func:`parse_cds`. Anything else (CCS, agency-defined
    level 3/4 codes, reserved identifications) raises
    :class:`UnsupportedTimeCodeError`.
    """
    _available(data, offset, "time")
    time_code_id = (data[offset] >> 4) & 0b111
    if time_code_id in (_ID_CUC_LEVEL_1, _ID_CUC_LEVEL_2):
        return parse_cuc(data, offset=offset, epoch=epoch)
    if time_code_id == _ID_CDS:
        return parse_cds(data, offset=offset, epoch=epoch)
    raise UnsupportedTimeCodeError(
        f"unsupported time code identification {time_code_id:03b}; only "
        "CUC (0b001/0b010) and CDS (0b100) are supported"
    )


def _available(data: bytes | bytearray | memoryview, offset: int, what: str) -> int:
    if offset < 0:
        raise ValueError("offset must be >= 0")
    available = len(data) - offset
    if available < 1:
        raise TruncatedPacketError(
            f"no bytes at offset {offset} for a {what} time code"
        )
    return available


def _require(available: int, needed: int, what: str) -> None:
    if available < needed:
        raise TruncatedPacketError(
            f"{what} time code needs {needed} bytes, "
            f"but only {available} remain"
        )


def _resolve_epoch(epoch: datetime | None, level: int) -> datetime:
    if epoch is None:
        if level == 2:
            raise UnsupportedTimeCodeError(
                "level 2 time code with an agency-defined epoch; "
                "pass the mission epoch via epoch="
            )
        return CCSDS_EPOCH
    if epoch.tzinfo is None:
        return epoch.replace(tzinfo=timezone.utc)
    return epoch


def _add(epoch: datetime, raw: bytes | bytearray | memoryview, **delta: int) -> datetime:
    try:
        return epoch + timedelta(**delta)
    except OverflowError:
        raise InvalidPacketError(
            f"time code {bytes(raw).hex()} resolves to a date outside the "
            "range representable by datetime (years 1-9999)"
        ) from None

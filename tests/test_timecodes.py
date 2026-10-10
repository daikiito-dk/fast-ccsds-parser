"""Tests for CUC / CDS time code decoding (CCSDS 301.0-B-4).

The expected values are hand-computed from the standard: the CCSDS epoch is
1958-01-01T00:00:00, CUC coarse time counts seconds and fine time counts
2**-(8n) seconds, CDS carries days + milliseconds of day + a sub-millisecond
segment (microseconds for 16 bits, picoseconds for 32 bits).
"""

from datetime import datetime, timedelta, timezone

import pytest

from fast_ccsds_parser import (
    CCSDS_EPOCH,
    InvalidPacketError,
    TruncatedPacketError,
    UnsupportedTimeCodeError,
    parse_cds,
    parse_cuc,
    parse_timecode,
    scan_bytes,
)

EPOCH = datetime(1958, 1, 1, tzinfo=timezone.utc)

#: Days from 1958-01-01 to 2000-01-01: 42 years x 365 + 10 leap days
#: (1960, 1964, ..., 1996).
DAYS_1958_TO_2000 = 15340


# -- CUC -----------------------------------------------------------------------


def test_cuc_epoch_is_ccsds_epoch():
    # P-field 0x1E: id 0b001, 4 coarse octets, 2 fine octets.
    tc = parse_cuc(b"\x1e\x00\x00\x00\x00\x00\x00")
    assert tc.timestamp == EPOCH
    assert tc.format == "cuc"
    assert tc.level == 1
    assert tc.preamble == 0x1E
    assert tc.n_bytes == 7


def test_cuc_coarse_time_counts_seconds():
    # 86400 seconds = exactly one day after the epoch.
    tc = parse_cuc(b"\x1e\x00\x01\x51\x80\x00\x00")
    assert tc.timestamp == datetime(1958, 1, 2, tzinfo=timezone.utc)


def test_cuc_absolute_date():
    coarse = DAYS_1958_TO_2000 * 86400  # seconds from 1958-01-01 to 2000-01-01
    data = b"\x1c" + coarse.to_bytes(4, "big")  # 4 coarse octets, no fine
    assert parse_cuc(data).timestamp == datetime(2000, 1, 1, tzinfo=timezone.utc)


def test_cuc_fine_time_is_a_binary_fraction():
    # P-field 0x1D: 4 coarse octets, 1 fine octet; 0x80 = 2**-1 s.
    tc = parse_cuc(b"\x1d\x00\x00\x00\x00\x80")
    assert tc.timestamp == EPOCH + timedelta(milliseconds=500)
    assert tc.n_bytes == 6


def test_cuc_fine_time_three_octets():
    # P-field 0x1F: 4 coarse, 3 fine octets; 0x800000 = 2**-1 s.
    tc = parse_cuc(b"\x1f\x00\x00\x00\x00\x80\x00\x00")
    assert tc.timestamp == EPOCH + timedelta(milliseconds=500)
    # 2**-24 s (about 60 ns) is below datetime resolution and truncated.
    assert parse_cuc(b"\x1f\x00\x00\x00\x00\x00\x00\x01").timestamp == EPOCH


def test_cuc_minimal_form():
    # P-field 0x10: 1 coarse octet, no fine octets.
    tc = parse_cuc(b"\x10\x2a")
    assert tc.timestamp == EPOCH + timedelta(seconds=42)
    assert tc.n_bytes == 2


def test_cuc_pfield_extension_octet():
    # P-field 0x9E: extension flag set; second octet 0x04 adds 1 fine octet.
    tc = parse_cuc(b"\x9e\x04\x00\x00\x00\x00\x80\x00\x00")
    assert tc.timestamp == EPOCH + timedelta(milliseconds=500)
    assert tc.n_bytes == 9
    assert tc.preamble == 0x9E


def test_cuc_third_pfield_octet_is_undefined():
    with pytest.raises(UnsupportedTimeCodeError):
        parse_cuc(b"\x9e\x84\x00\x00\x00\x00\x00\x00\x00")


def test_cuc_level2_needs_an_epoch():
    # P-field 0x2C: id 0b010 (agency-defined epoch), 4 coarse, 0 fine.
    with pytest.raises(UnsupportedTimeCodeError):
        parse_cuc(b"\x2c\x00\x00\x00\x00")


def test_cuc_level2_with_custom_epoch():
    tc = parse_cuc(b"\x2c\x00\x00\x00\x3c", epoch=datetime(2000, 1, 1))
    assert tc.level == 2
    # A naive epoch is assumed UTC.
    assert tc.timestamp == datetime(2000, 1, 1, 0, 1, tzinfo=timezone.utc)


def test_cuc_aware_epoch_is_kept():
    tz9 = timezone(timedelta(hours=9))
    tc = parse_cuc(b"\x2c\x00\x00\x00\x00", epoch=datetime(2000, 1, 1, tzinfo=tz9))
    assert tc.timestamp == datetime(2000, 1, 1, tzinfo=tz9)


def test_cuc_rejects_other_time_code_ids():
    with pytest.raises(UnsupportedTimeCodeError):
        parse_cuc(b"\x40\x00\x00\x00\x00\x00\x00")  # 0b100 = CDS


def test_cuc_truncated():
    with pytest.raises(TruncatedPacketError):
        parse_cuc(b"\x1e\x00")  # declares 6 T-field bytes, has 1
    with pytest.raises(TruncatedPacketError):
        parse_cuc(b"\x9e")  # extension flag set but second octet missing
    with pytest.raises(TruncatedPacketError):
        parse_cuc(b"")


def test_cuc_offset_and_memoryview():
    data = b"\xaa\xbb" + b"\x1e\x00\x00\x00\x00\x00\x00"
    assert parse_cuc(memoryview(data), offset=2).timestamp == EPOCH
    with pytest.raises(ValueError, match="offset"):
        parse_cuc(data, offset=-1)


# -- CDS -----------------------------------------------------------------------


def test_cds_short_epoch():
    # P-field 0x40: id 0b100, level 1, 16-bit day, no sub-milliseconds.
    tc = parse_cds(b"\x40\x00\x00\x00\x00\x00\x00")
    assert tc.timestamp == EPOCH
    assert tc.format == "cds"
    assert tc.level == 1
    assert tc.preamble == 0x40
    assert tc.n_bytes == 7


def test_cds_day_segment():
    tc = parse_cds(b"\x40\x00\x01\x00\x00\x00\x00")
    assert tc.timestamp == datetime(1958, 1, 2, tzinfo=timezone.utc)


def test_cds_milliseconds_of_day():
    tc = parse_cds(b"\x40\x00\x00\x00\x00\x03\xe8")  # 1000 ms
    assert tc.timestamp == EPOCH + timedelta(seconds=1)


def test_cds_absolute_date():
    data = b"\x40" + DAYS_1958_TO_2000.to_bytes(2, "big") + (0).to_bytes(4, "big")
    assert parse_cds(data).timestamp == datetime(2000, 1, 1, tzinfo=timezone.utc)


def test_cds_microsecond_segment():
    # P-field 0x41: 16-bit sub-millisecond segment counting microseconds.
    tc = parse_cds(b"\x41\x00\x00\x00\x00\x00\x00\x01\xf4")  # 500 us
    assert tc.timestamp == EPOCH + timedelta(microseconds=500)
    assert tc.n_bytes == 9


def test_cds_picosecond_segment_and_24bit_day():
    # P-field 0x46: 24-bit day, 32-bit sub-millisecond segment (picoseconds).
    # 999,999,999 ps = 999.999999 us, truncated to 999 us.
    tc = parse_cds(b"\x46\x00\x00\x01\x00\x00\x00\x00\x3b\x9a\xc9\xff")
    assert tc.timestamp == datetime(1958, 1, 2, tzinfo=timezone.utc) + timedelta(
        microseconds=999
    )
    assert tc.n_bytes == 12


def test_cds_leap_day_milliseconds_roll_into_the_next_day():
    # Annex A allows up to 86,400,999 ms on days with leap adjustments.
    ms = 86_400_000
    data = b"\x40\x00\x01" + ms.to_bytes(4, "big")
    assert parse_cds(data).timestamp == datetime(1958, 1, 3, tzinfo=timezone.utc)


def test_cds_rejects_milliseconds_beyond_the_leap_day_range():
    ms = 86_401_000  # past the annex A maximum of 86,400,999
    with pytest.raises(InvalidPacketError):
        parse_cds(b"\x40\x00\x00" + ms.to_bytes(4, "big"))


def test_cds_rejects_out_of_range_submilliseconds():
    with pytest.raises(InvalidPacketError):
        parse_cds(b"\x41\x00\x00\x00\x00\x00\x00\x03\xe8")  # 1000 > 999 us
    with pytest.raises(InvalidPacketError):
        # 1,000,000,000 > 999,999,999 ps
        parse_cds(b"\x42\x00\x00\x00\x00\x00\x00\x3b\x9a\xca\x00")


def test_cds_reserved_submillisecond_code():
    with pytest.raises(UnsupportedTimeCodeError):
        parse_cds(b"\x43\x00\x00\x00\x00\x00\x00\x00")


def test_cds_rejects_pfield_extension():
    with pytest.raises(UnsupportedTimeCodeError):
        parse_cds(b"\xc0\x00\x00\x00\x00\x00\x00")


def test_cds_level2_needs_an_epoch():
    # P-field 0x48: level 2 (agency-defined epoch).
    with pytest.raises(UnsupportedTimeCodeError):
        parse_cds(b"\x48\x00\x00\x00\x00\x00\x00")
    tc = parse_cds(
        b"\x48\x00\x01\x00\x00\x00\x00", epoch=datetime(2000, 1, 1, tzinfo=timezone.utc)
    )
    assert tc.level == 2
    assert tc.timestamp == datetime(2000, 1, 2, tzinfo=timezone.utc)


def test_cds_rejects_other_time_code_ids():
    with pytest.raises(UnsupportedTimeCodeError):
        parse_cds(b"\x1e\x00\x00\x00\x00\x00\x00")  # 0b001 = CUC


def test_cds_truncated():
    with pytest.raises(TruncatedPacketError):
        parse_cds(b"\x40\x00")  # declares a 6-byte T-field, has 1
    with pytest.raises(TruncatedPacketError):
        parse_cds(b"")


def test_cds_day_beyond_datetime_range():
    # 24-bit day = 0xFFFFFF lands around year 47700, outside datetime.
    with pytest.raises(InvalidPacketError):
        parse_cds(b"\x44\xff\xff\xff\x00\x00\x00\x00")


# -- parse_timecode dispatch ---------------------------------------------------


def test_parse_timecode_dispatches_on_the_pfield():
    assert parse_timecode(b"\x1e\x00\x00\x00\x00\x00\x00").format == "cuc"
    assert parse_timecode(b"\x40\x00\x00\x00\x00\x00\x00").format == "cds"
    assert parse_timecode(b"\x2c\x00\x00\x00\x00", epoch=CCSDS_EPOCH).format == "cuc"


def test_parse_timecode_rejects_unsupported_ids():
    for preamble in (0x00, 0x30, 0x50, 0x60, 0x70):  # reserved/reserved/CCS/agency/reserved
        with pytest.raises(UnsupportedTimeCodeError):
            parse_timecode(bytes([preamble]) + b"\x00" * 16)
    with pytest.raises(TruncatedPacketError):
        parse_timecode(b"")


# -- packet and table integration ----------------------------------------------


def test_space_packet_timecode(build_packet):
    payload = b"\x40\x00\x01\x00\x00\x00\x00" + b"USER"
    packet_bytes = build_packet(payload=payload, secondary_header_flag=1)
    from fast_ccsds_parser import parse_packet

    packet = parse_packet(packet_bytes)
    tc = packet.timecode()
    assert tc.timestamp == datetime(1958, 1, 2, tzinfo=timezone.utc)
    # n_bytes locates the user data behind the time code.
    assert packet.payload[tc.n_bytes :] == b"USER"


def test_space_packet_timecode_with_offset(build_packet):
    from fast_ccsds_parser import parse_packet

    payload = b"\x00\x11" + b"\x40\x00\x01\x00\x00\x00\x00"
    packet = parse_packet(build_packet(payload=payload))
    assert packet.timecode(offset=2).timestamp == datetime(
        1958, 1, 2, tzinfo=timezone.utc
    )
    # A packet whose data field holds no P-field fails cleanly.
    with pytest.raises(UnsupportedTimeCodeError):
        packet.timecode()


def test_packet_table_timecodes(build_packet):
    day1 = b"\x40\x00\x01\x00\x00\x00\x00"
    day2 = b"\x40\x00\x02\x00\x00\x00\x00"
    data = build_packet(payload=day1) + build_packet(payload=day2)
    table = scan_bytes(data)
    assert table.timecodes() == [
        datetime(1958, 1, 2, tzinfo=timezone.utc),
        datetime(1958, 1, 3, tzinfo=timezone.utc),
    ]


def test_packet_table_timecodes_with_offset(build_packet):
    payload = b"\x99" + b"\x40\x00\x01\x00\x00\x00\x00"
    table = scan_bytes(build_packet(payload=payload))
    assert table.timecodes(offset=1) == [datetime(1958, 1, 2, tzinfo=timezone.utc)]


def test_packet_table_timecodes_without_payload(build_packet):
    table = scan_bytes(build_packet(payload=b"\x40\x00\x00\x00\x00\x00\x00"),
                       payload=False)
    with pytest.raises(ValueError, match="payload=False"):
        table.timecodes()

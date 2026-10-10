"""Tests for fast_ccsds_parser.header."""

import pytest

from fast_ccsds_parser import (
    IDLE_APID,
    PRIMARY_HEADER_SIZE,
    InvalidPacketError,
    PacketType,
    PrimaryHeader,
    SequenceFlags,
    TruncatedPacketError,
    parse_primary_header,
)

# version=0, type=0 (TM), sec-flag=0, APID=1, seq-flags=0b11 (unsegmented),
# count=0, packet_length=3 (4-byte data field): a textbook minimal header.
KNOWN_HEADER = bytes.fromhex("0001c0000003")


class TestPrimaryHeaderParsing:
    def test_known_header_fields(self):
        header = PrimaryHeader.from_bytes(KNOWN_HEADER)
        assert header.version == 0
        assert header.type == PacketType.TELEMETRY
        assert header.secondary_header_flag == 0
        assert header.apid == 1
        assert header.sequence_flags == SequenceFlags.UNSEGMENTED
        assert header.sequence_count == 0
        assert header.packet_length == 3

    def test_known_header_derived_lengths(self):
        header = PrimaryHeader.from_bytes(KNOWN_HEADER)
        assert header.data_field_length == 4  # packet_length + 1
        assert header.total_length == PRIMARY_HEADER_SIZE + 4

    def test_all_fields_maximal(self):
        header = PrimaryHeader.from_bytes(b"\xff" * 6, strict=False)
        assert tuple(header) == (7, 1, 1, IDLE_APID, 0b11, 0x3FFF, 0xFFFF)
        assert header.has_secondary_header
        assert header.is_idle
        assert header.data_field_length == 0x10000
        assert header.total_length == 0x10006

    def test_strict_rejects_nonzero_version(self):
        with pytest.raises(InvalidPacketError, match="version"):
            PrimaryHeader.from_bytes(b"\xff" * 6)

    def test_non_strict_parses_nonzero_version(self):
        header = PrimaryHeader.from_bytes(b"\xff" * 6, strict=False)
        assert header.version == 7

    def test_offset_parsing(self):
        header = PrimaryHeader.from_bytes(b"\x12\x34\x56" + KNOWN_HEADER, offset=3)
        assert header == PrimaryHeader.from_bytes(KNOWN_HEADER)

    def test_truncated_header(self):
        with pytest.raises(TruncatedPacketError, match="6 bytes"):
            PrimaryHeader.from_bytes(b"\x00\x01\xc0")
        with pytest.raises(TruncatedPacketError):
            PrimaryHeader.from_bytes(b"")

    def test_bytes_like_inputs(self):
        expected = PrimaryHeader.from_bytes(KNOWN_HEADER)
        assert PrimaryHeader.from_bytes(bytearray(KNOWN_HEADER)) == expected
        assert PrimaryHeader.from_bytes(memoryview(KNOWN_HEADER)) == expected

    def test_namedtuple_shape(self):
        header = PrimaryHeader.from_bytes(KNOWN_HEADER)
        assert isinstance(header, tuple)
        assert header._fields == (
            "version",
            "type",
            "secondary_header_flag",
            "apid",
            "sequence_flags",
            "sequence_count",
            "packet_length",
        )
        assert header[3] == header.apid

    def test_secondary_header_flag(self):
        sec_header = bytes.fromhex("08640000") + b"\x00\x00"
        header = PrimaryHeader.from_bytes(sec_header)
        assert header.secondary_header_flag == 1
        assert header.has_secondary_header
        assert header.apid == 0x064

    def test_idle_apid(self):
        idle = bytes.fromhex("07ffc000") + b"\x00\x00"
        header = PrimaryHeader.from_bytes(idle)
        assert header.apid == IDLE_APID
        assert header.is_idle

    def test_to_dict(self):
        header = PrimaryHeader.from_bytes(KNOWN_HEADER)
        assert header.to_dict() == {
            "version": 0,
            "type": 0,
            "secondary_header_flag": 0,
            "apid": 1,
            "sequence_flags": 3,
            "sequence_count": 0,
            "packet_length": 3,
        }

    def test_parse_primary_header_dict_shortcut(self):
        assert parse_primary_header(KNOWN_HEADER) == (
            PrimaryHeader.from_bytes(KNOWN_HEADER).to_dict()
        )

    def test_enum_values(self):
        assert PacketType.TELEMETRY == 0
        assert PacketType.TELECOMMAND == 1
        assert SequenceFlags.CONTINUATION == 0b00
        assert SequenceFlags.FIRST_SEGMENT == 0b01
        assert SequenceFlags.LAST_SEGMENT == 0b10
        assert SequenceFlags.UNSEGMENTED == 0b11

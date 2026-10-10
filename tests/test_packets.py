"""Tests for fast_ccsds_parser.packets."""

import pytest

from fast_ccsds_parser import (
    InvalidPacketError,
    SpacePacket,
    TruncatedPacketError,
    iter_file,
    parse_file,
    parse_packet,
    parse_packets,
)


class TestParsePacket:
    def test_roundtrip(self, build_packet):
        payload = bytes(range(16))
        raw = build_packet(apid=0x5A5, sequence_count=5, payload=payload)
        pkt = parse_packet(raw)
        assert isinstance(pkt, SpacePacket)
        assert pkt.header.apid == 0x5A5
        assert pkt.header.sequence_count == 5
        assert pkt.header.sequence_flags == 0b11
        assert pkt.header.total_length == len(raw)
        assert pkt.payload == payload
        assert pkt.raw == raw

    def test_minimal_data_field(self, build_packet):
        pkt = parse_packet(build_packet(payload=b"\x42"))
        assert pkt.header.packet_length == 0
        assert pkt.payload == b"\x42"

    def test_rejects_trailing_bytes(self, build_packet):
        raw = build_packet(payload=b"\x01\x02") + b"\xff"
        with pytest.raises(InvalidPacketError, match="trailing"):
            parse_packet(raw)

    def test_truncated_data_field(self, build_packet):
        raw = build_packet(payload=b"\x01\x02\x03")
        with pytest.raises(TruncatedPacketError, match="total"):
            parse_packet(raw[:-1])

    def test_rejects_wrong_version(self, build_packet):
        raw = build_packet(version=3, payload=b"\x01")
        with pytest.raises(InvalidPacketError):
            parse_packet(raw)
        assert parse_packet(raw, strict=False).header.version == 3

    def test_payload_keeps_secondary_header_bytes(self, build_packet):
        data_field = b"\x01\x02" + b"user-data"
        raw = build_packet(secondary_header_flag=1, payload=data_field)
        pkt = parse_packet(raw)
        assert pkt.header.has_secondary_header
        # The data field is returned as-is; splitting secondary header from
        # user data is mission-specific and out of scope for the structural
        # parser (see SpacePacket docs).
        assert pkt.payload == data_field

    def test_to_dict_shape(self, build_packet):
        pkt = parse_packet(build_packet(apid=0x42, payload=b"\x10\x20"))
        d = pkt.to_dict()
        assert set(d) == {
            "version",
            "type",
            "secondary_header_flag",
            "apid",
            "sequence_flags",
            "sequence_count",
            "packet_length",
            "payload",
        }
        assert d["apid"] == 0x42
        assert d["payload"] == b"\x10\x20"


class TestIterPackets:
    def test_walks_multiple_packets(self, build_packet):
        packets = [
            build_packet(apid=1, sequence_count=0, payload=b"a"),
            build_packet(apid=2, sequence_count=1, payload=b"bb"),
            build_packet(apid=1, sequence_count=2, payload=b"ccc"),
        ]
        buffer = b"".join(packets)
        parsed = parse_packets(buffer)
        assert [p.header.apid for p in parsed] == [1, 2, 1]
        assert [p.header.sequence_count for p in parsed] == [0, 1, 2]
        assert [p.payload for p in parsed] == [b"a", b"bb", b"ccc"]
        assert b"".join(p.raw for p in parsed) == buffer

    def test_yields_idle_packets_by_default(self, build_packet):
        normal = build_packet(apid=0x100, payload=b"data")
        idle = build_packet(apid=0x7FF, payload=b"\x55\x55")
        buffer = normal + idle + normal
        assert len(parse_packets(buffer)) == 3

    def test_skip_idle(self, build_packet):
        normal = build_packet(apid=0x100, payload=b"data")
        idle = build_packet(apid=0x7FF, payload=b"\x55\x55")
        buffer = normal + idle + normal
        parsed = parse_packets(buffer, skip_idle=True)
        assert len(parsed) == 2
        assert all(not p.header.is_idle for p in parsed)

    def test_trailing_partial_header(self, build_packet):
        buffer = build_packet(payload=b"x") + b"\x00\x01"
        with pytest.raises(TruncatedPacketError, match="offset 7"):
            parse_packets(buffer)

    def test_declared_length_exceeds_buffer(self, build_packet):
        raw = build_packet(apid=9, payload=b"\x01" * 10)
        with pytest.raises(TruncatedPacketError, match="APID 9"):
            parse_packets(raw[:-3])

    def test_strict_version_error(self, build_packet):
        buffer = (
            build_packet(apid=1, payload=b"aa")
            + build_packet(apid=2, version=2, payload=b"bb")
        )
        with pytest.raises(InvalidPacketError, match="version"):
            parse_packets(buffer)
        assert parse_packets(buffer, strict=False)[1].header.version == 2

    def test_memoryview_input(self, build_packet):
        buffer = b"".join(
            build_packet(apid=k, payload=bytes([k])) for k in range(4)
        )
        assert len(parse_packets(memoryview(buffer))) == 4

    def test_large_buffer(self, build_packet):
        buffer = build_packet(apid=0x200, payload=b"\x00" * 32) * 5000
        packets = parse_packets(buffer)
        assert len(packets) == 5000
        assert sum(len(p.raw) for p in packets) == len(buffer)


class TestIterFile:
    def test_streams_packets(self, build_packet, tmp_path):
        packets = [
            build_packet(
                apid=0x10,
                sequence_count=n,
                payload=bytes([n]) * (n % 5 + 1),
            )
            for n in range(25)
        ]
        path = tmp_path / "dump.pkts"
        path.write_bytes(b"".join(packets))
        from_file = parse_file(path)
        assert from_file == parse_packets(b"".join(packets))
        assert [p.header.sequence_count for p in from_file] == list(range(25))

    def test_is_lazy(self, build_packet, tmp_path):
        path = tmp_path / "dump.pkts"
        path.write_bytes(b"".join(build_packet(payload=b"z") for _ in range(5)))
        stream = iter_file(path)
        assert next(stream).payload == b"z"
        assert sum(1 for _ in stream) == 4

    def test_truncated_tail(self, build_packet, tmp_path):
        raw = build_packet(apid=7, payload=b"\x01" * 8)
        path = tmp_path / "short.pkts"
        path.write_bytes(raw[:10])  # full header + only 4 of 8 data-field bytes
        with pytest.raises(TruncatedPacketError, match="mid-packet"):
            parse_file(path)

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.pkts"
        path.write_bytes(b"")
        assert parse_file(path) == []
        assert list(iter_file(path)) == []

    def test_skip_idle(self, build_packet, tmp_path):
        buffer = build_packet(apid=0x7FF, payload=b"\x55") + build_packet(
            apid=1, payload=b"\x66"
        )
        path = tmp_path / "mixed.pkts"
        path.write_bytes(buffer)
        assert [p.header.apid for p in parse_file(path, skip_idle=True)] == [1]

    def test_wrong_version_strict(self, build_packet, tmp_path):
        path = tmp_path / "bad.pkts"
        path.write_bytes(build_packet(version=1, payload=b"\x01"))
        with pytest.raises(InvalidPacketError):
            parse_file(path)

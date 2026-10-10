"""Tests for fast_ccsds_parser.table (columnar scanning and export)."""

from array import array

import pytest

from fast_ccsds_parser import (
    InvalidPacketError,
    PacketTable,
    TruncatedPacketError,
    iter_tables,
    parse_packets,
    scan_bytes,
    scan_file,
    write_parquet,
)

HEADER_COLUMNS = [
    "version",
    "type",
    "secondary_header_flag",
    "apid",
    "sequence_flags",
    "sequence_count",
    "packet_length",
]


@pytest.fixture
def mixed_buffer(build_packet):
    """Packets that exercise every header field and varied payload sizes."""
    return b"".join(
        build_packet(
            apid=(n * 37) % 0x7FF,
            sequence_count=(n * 1000) % 0x4000,
            packet_type=n % 2,
            secondary_header_flag=(n // 2) % 2,
            sequence_flags=n % 4,
            payload=bytes([n % 256]) * (n % 9 + 1),
        )
        for n in range(200)
    )


def assert_table_matches_packets(table, packets):
    assert len(table) == len(packets)
    for column in HEADER_COLUMNS:
        assert list(getattr(table, column)) == [getattr(p.header, column) for p in packets]
    if table.has_payload:
        assert [table.payload(i) for i in range(len(table))] == [p.payload for p in packets]


class TestScanBytes:
    def test_matches_object_parser(self, mixed_buffer):
        table = scan_bytes(mixed_buffer)
        assert isinstance(table, PacketTable)
        packets = parse_packets(mixed_buffer)
        assert_table_matches_packets(table, packets)
        expected_offsets, position = [], 0
        for p in packets:
            expected_offsets.append(position)
            position += len(p.raw)
        assert list(table.offset) == expected_offsets

    def test_columns_are_typed_arrays(self, mixed_buffer):
        table = scan_bytes(mixed_buffer)
        assert isinstance(table.apid, array) and table.apid.typecode == "H"
        assert isinstance(table.offset, array) and table.offset.typecode == "Q"

    @pytest.mark.parametrize("kind", [bytes, bytearray, memoryview])
    def test_bytes_like_inputs(self, mixed_buffer, kind):
        assert list(scan_bytes(kind(mixed_buffer)).apid) == list(scan_bytes(mixed_buffer).apid)

    def test_without_payload(self, mixed_buffer):
        table = scan_bytes(mixed_buffer, payload=False)
        assert not table.has_payload
        assert_table_matches_packets(table, parse_packets(mixed_buffer))
        with pytest.raises(ValueError, match="payload=False"):
            table.payload(0)

    def test_payload_negative_and_out_of_range_index(self, build_packet):
        table = scan_bytes(build_packet(payload=b"a") + build_packet(payload=b"bc"))
        assert table.payload(-1) == b"bc"
        with pytest.raises(IndexError):
            table.payload(2)

    def test_empty(self):
        table = scan_bytes(b"")
        assert len(table) == 0
        assert list(table.apid) == []

    def test_skip_idle(self, build_packet):
        buffer = (
            build_packet(apid=1, payload=b"a")
            + build_packet(apid=0x7FF, payload=b"idle")
            + build_packet(apid=2, payload=b"b")
        )
        table = scan_bytes(buffer, skip_idle=True)
        assert list(table.apid) == [1, 2]
        assert [table.payload(i) for i in range(2)] == [b"a", b"b"]
        # Offsets still point into the original buffer: 7-byte packet, then a
        # 10-byte idle packet that was skipped.
        assert list(table.offset) == [0, 17]

    def test_strict_version(self, build_packet):
        buffer = build_packet(apid=1, payload=b"a") + build_packet(version=2, payload=b"b")
        with pytest.raises(InvalidPacketError, match="version"):
            scan_bytes(buffer)
        assert list(scan_bytes(buffer, strict=False).version) == [0, 2]

    def test_truncated_header(self, build_packet):
        with pytest.raises(TruncatedPacketError, match="offset 7"):
            scan_bytes(build_packet(payload=b"x") + b"\x00\x01")

    def test_truncated_data_field(self, build_packet):
        raw = build_packet(apid=9, payload=b"\x01" * 10)
        with pytest.raises(TruncatedPacketError, match="APID 9"):
            scan_bytes(raw[:-3])

    def test_largest_packet(self, build_packet):
        payload = bytes(range(256)) * 256  # 65,536 bytes: packet_length = 0xFFFF
        table = scan_bytes(build_packet(payload=payload) * 2)
        assert list(table.packet_length) == [0xFFFF, 0xFFFF]
        assert table.payload(1) == payload


class TestScanFile:
    def test_matches_scan_bytes(self, mixed_buffer, tmp_path):
        path = tmp_path / "dump.bin"
        path.write_bytes(mixed_buffer)
        assert_table_matches_packets(scan_file(path), parse_packets(mixed_buffer))
        assert list(scan_file(path).offset) == list(scan_bytes(mixed_buffer).offset)

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.bin"
        path.write_bytes(b"")
        assert len(scan_file(path)) == 0
        assert len(scan_file(path, payload=False)) == 0
        assert list(iter_tables(path)) == []

    def test_only_idle_packets(self, build_packet, tmp_path):
        path = tmp_path / "idle.bin"
        path.write_bytes(build_packet(apid=0x7FF, payload=b"zz") * 3)
        assert len(scan_file(path, skip_idle=True)) == 0

    def test_truncated_tail(self, build_packet, tmp_path):
        path = tmp_path / "short.bin"
        path.write_bytes(build_packet(payload=b"\x01" * 8)[:10])
        with pytest.raises(TruncatedPacketError):
            scan_file(path)


class TestIterTables:
    def test_chunks_concatenate_to_whole(self, mixed_buffer, tmp_path):
        path = tmp_path / "dump.bin"
        path.write_bytes(mixed_buffer)
        chunks = list(iter_tables(path, chunk_packets=64))
        assert [len(c) for c in chunks] == [64, 64, 64, 8]
        whole = scan_file(path)
        for column in ["offset", *HEADER_COLUMNS]:
            joined = [v for c in chunks for v in getattr(c, column)]
            assert joined == list(getattr(whole, column))
        payloads = [c.payload(i) for c in chunks for i in range(len(c))]
        assert payloads == [whole.payload(i) for i in range(len(whole))]

    def test_chunk_counts_kept_packets(self, build_packet, tmp_path):
        idle = build_packet(apid=0x7FF, payload=b"i")
        data = build_packet(apid=5, payload=b"d")
        path = tmp_path / "mixed.bin"
        path.write_bytes((idle + data) * 5)
        chunks = list(iter_tables(path, chunk_packets=2, skip_idle=True))
        assert [len(c) for c in chunks] == [2, 2, 1]
        assert all(set(c.apid) == {5} for c in chunks)

    def test_rejects_bad_chunk_size(self, tmp_path):
        path = tmp_path / "x.bin"
        path.write_bytes(b"")
        with pytest.raises(ValueError, match="chunk_packets"):
            list(iter_tables(path, chunk_packets=0))


class TestArrowExport:
    def test_schema_and_values(self, mixed_buffer):
        pa = pytest.importorskip("pyarrow")
        table = scan_bytes(mixed_buffer)
        arrow = table.to_arrow()
        assert arrow.schema.field("offset").type == pa.uint64()
        assert arrow.schema.field("apid").type == pa.uint16()
        assert arrow.schema.field("version").type == pa.uint8()
        assert arrow.schema.field("payload").type == pa.large_binary()
        packets = parse_packets(mixed_buffer)
        for column in HEADER_COLUMNS:
            assert arrow.column(column).to_pylist() == [getattr(p.header, column) for p in packets]
        assert arrow.column("payload").to_pylist() == [p.payload for p in packets]

    def test_without_payload(self, mixed_buffer):
        pytest.importorskip("pyarrow")
        arrow = scan_bytes(mixed_buffer, payload=False).to_arrow()
        assert arrow.column_names == ["offset", *HEADER_COLUMNS]

    def test_empty(self):
        pytest.importorskip("pyarrow")
        assert scan_bytes(b"").to_arrow().num_rows == 0

    def test_polars(self, mixed_buffer):
        pl = pytest.importorskip("polars")
        pytest.importorskip("pyarrow")
        df = scan_bytes(mixed_buffer).to_polars()
        assert isinstance(df, pl.DataFrame)
        packets = parse_packets(mixed_buffer)
        assert df["apid"].to_list() == [p.header.apid for p in packets]
        assert df["payload"].to_list() == [p.payload for p in packets]

    def test_pandas(self, mixed_buffer):
        pd = pytest.importorskip("pandas")
        pytest.importorskip("pyarrow")
        df = scan_bytes(mixed_buffer).to_pandas()
        packets = parse_packets(mixed_buffer)
        assert str(df["apid"].dtype) == "uint16"
        assert isinstance(df["payload"].dtype, pd.ArrowDtype)
        assert df["sequence_count"].tolist() == [p.header.sequence_count for p in packets]
        assert df["payload"].tolist() == [p.payload for p in packets]


class TestWriteParquet:
    def test_roundtrip(self, mixed_buffer, tmp_path):
        pytest.importorskip("pyarrow")
        import pyarrow.parquet as pq

        source = tmp_path / "dump.bin"
        source.write_bytes(mixed_buffer)
        destination = tmp_path / "dump.parquet"
        assert write_parquet(source, destination, chunk_packets=50) == 200
        assert pq.ParquetFile(destination).num_row_groups == 4
        assert pq.read_table(destination).equals(scan_file(source).to_arrow())

    def test_empty_file_writes_schema(self, tmp_path):
        pytest.importorskip("pyarrow")
        import pyarrow.parquet as pq

        source = tmp_path / "empty.bin"
        source.write_bytes(b"")
        destination = tmp_path / "empty.parquet"
        assert write_parquet(source, destination, payload=False) == 0
        result = pq.read_table(destination)
        assert result.num_rows == 0
        assert result.column_names == ["offset", *HEADER_COLUMNS]


def test_missing_pyarrow_message(mixed_buffer, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("pyarrow"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match=r"fast-ccsds-parser\[arrow\]"):
        scan_bytes(mixed_buffer).to_arrow()

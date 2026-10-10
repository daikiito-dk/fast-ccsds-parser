# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-10-10

### Added

- Columnar parsing (Phase 2a): `scan_bytes` and `scan_file` parse a buffer or
  a whole (memory-mapped) file into a `PacketTable`, which keeps header fields
  in typed `array.array` columns and payloads in one contiguous buffer.
  Memory stays close to the input size; pass `payload=False` to keep only the
  header columns.
- `iter_tables`: yields one `PacketTable` per chunk (default 1,000,000
  packets), so files larger than memory can be processed.
- `write_parquet`: converts a packet file to Parquet chunk by chunk, bounded
  memory, one row group per chunk (needs `pyarrow`).
- `PacketTable.to_arrow()` (zero-copy header columns), `to_polars()`, and
  `to_pandas()` (Arrow-backed payload column).
- Optional extras: `arrow` (pyarrow), `polars`, `pandas`, and `all`. The core
  still has no dependencies.
- Benchmarks for the columnar API in `benchmarks/bench.py`; on 1,000,000
  synthetic packets, `scan_file().to_pandas()` is about 5x faster than
  building from per-packet dicts and uses about a quarter of the memory.

## [0.1.0] - 2026-10-10

### Added

- Initial release (Phase 1: pure-Python prototype).
- `PrimaryHeader` / `parse_primary_header`: decode the 6-byte CCSDS 133.0-B-2
  primary header (version, type, secondary header flag, APID, sequence flags,
  sequence count, packet length) as a `NamedTuple` or a `dict`.
- `parse_packet` / `iter_packets` / `parse_packets`: split concatenated packets
  in a buffer and separate payloads.
- `iter_file` / `parse_file`: stream packets from dump files with constant
  memory (gigabyte-scale logs are fine).
- Strict mode (default) rejecting non-space-packet versions, and a
  `skip_idle` option for idle packets.
- Pure standard-library core: no runtime dependencies.

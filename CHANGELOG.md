# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

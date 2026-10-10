# fast-ccsds-parser

[![CI](https://github.com/daikiito-dk/fast-ccsds-parser/actions/workflows/ci.yml/badge.svg)](https://github.com/daikiito-dk/fast-ccsds-parser/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![TestPyPI](https://img.shields.io/badge/TestPyPI-0.1.0-orange)](https://test.pypi.org/project/fast-ccsds-parser/)

[日本語のドキュメントはこちら](README.ja.md)

Fast, dependency-free parsing of **CCSDS Space Packets** ([CCSDS 133.0-B-2])
for the modern Python data stack. Turn raw telemetry dumps into dicts and
structured objects in one pass: primary headers, packet boundaries, and
payloads, with a pure standard-library core that installs anywhere
(`pip install` and go).

Built for small-satellite teams, university labs, and data engineers who
just want their packets parsed, without pulling a decoding framework.

[CCSDS 133.0-B-2]: https://public.ccsds.org/Pubs/133x0b2e1.pdf

## Install

Until the first production release, install from TestPyPI:

```console
pip install --index-url https://test.pypi.org/simple/ fast-ccsds-parser
```

or from source:

```console
pip install git+https://github.com/daikiito-dk/fast-ccsds-parser.git
```

## Quickstart

```python
from fast_ccsds_parser import parse_primary_header, iter_file

# One header, as a plain dict:
header = parse_primary_header(raw_bytes)
# {'version': 0, 'type': 0, 'secondary_header_flag': 1, 'apid': 42,
#  'sequence_flags': 3, 'sequence_count': 1337, 'packet_length': 255}

# A whole ground-station dump, streamed packet by packet (constant memory):
for pkt in iter_file("dump_20261010.pkts"):
    print(pkt.header.apid, pkt.header.sequence_count, len(pkt.payload))

# In-memory buffers work the same way:
packets = [p.to_dict() for p in iter_packets(buffer)]
```

## What it parses

The 6-byte CCSDS primary header (all fields big-endian):

```text
bit:   0   1   2   3   4   5   6   7   8   9  10  11  12  13  14  15
       +---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+
0-1    | version  | T | S |                APID                       |
       +---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+
2-3    | seq flags |        sequence count / packet name               |
       +---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+
4-5    |                     packet length                            |
       +---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+---+
```

| Field                  | Bits | Meaning                                              |
| ---------------------- | ---- | ---------------------------------------------------- |
| `version`             | 3    | `0` for space packets                                |
| `type`                | 1    | 0 = telemetry, 1 = telecommand                       |
| `secondary_header_flag` | 1  | 1 = a secondary header follows the primary header    |
| `apid`                | 11   | application process identifier (all ones = idle)     |
| `sequence_flags`      | 2    | segmentation status (unsegmented = `0b11`)          |
| `sequence_count`      | 14   | packet sequence count / packet name                  |
| `packet_length`       | 16   | data field length **minus one** (see below)          |

## API

| Entry point | Purpose |
| ----------- | ------- |
| `parse_primary_header(data, *, offset=0, strict=True)` | Header fields as a `dict` |
| `PrimaryHeader.from_bytes(data, *, offset=0, strict=True)` | Header as a `NamedTuple` (`.apid`, `.total_length`, `.is_idle`, `.to_dict()`, ...) |
| `parse_packet(data, *, strict=True)` | Exactly one packet, as a `SpacePacket` |
| `iter_packets(data, *, strict=True, skip_idle=False)` | Generator over a buffer of concatenated packets |
| `parse_packets(data, ...)` | Same, as a `list` |
| `iter_file(path, ...)` / `parse_file(path, ...)` | Stream packets from a dump file with O(1) memory |

`SpacePacket` carries `header` (a `PrimaryHeader`), `payload` (the data
field bytes), `raw` (the complete packet bytes), and a `to_dict()` that
flattens everything into a single dict.

## Semantics to know

- **`packet_length` is zero-based.** The data field is `packet_length + 1`
  bytes, so a total packet is `6 + packet_length + 1` bytes. This is the
  number-one source of off-by-one bugs when handling CCSDS data, and the
  parser handles it for you.
- **`payload` = the full data field.** Per CCSDS 133.0-B-2 the data field is
  an optional secondary header followed by user data; the payload bytes are
  returned as-is. Check `header.has_secondary_header` to know whether a
  mission-specific secondary header is in front. Decoding those layouts is
  out of scope for the structural parser (see the roadmap).
- **Idle packets** (APID = `0x7FF`) are yielded like any other packet by
  default; pass `skip_idle=True` to filter them out.
- **Strict mode (default)** raises `InvalidPacketError` when the packet
  version is not 0, which usually means your byte stream is misaligned.
  Pass `strict=False` to parse anyway.
- All errors derive from `FastCCSDSError` (a `ValueError`).

## Positioning

| | fast-ccsds-parser | ccsdspy | space-packet-parser |
| --- | --- | --- | --- |
| Focus | Structural parsing: split and label packets, fast | Field-level decoding from packet definitions | Field-level decoding from XTCE definitions |
| Runtime dependencies | None | Yes | Yes |
| DataFrame / stream pipelines | Roadmap (Phase 2) | - | - |

If you need to *decode packet contents* against a mission packet database,
look at [ccsdspy] or [space-packet-parser]; both are excellent and battle
tested. fast-ccsds-parser does one thing: fast, dependency-free structural
parsing that is trivial to drop into a pandas or asyncio pipeline.

[ccsdspy]: https://github.com/CCSDSPy/ccsdspy
[space-packet-parser]: https://pypi.org/project/space-packet-parser/

## Performance & roadmap

The Phase 1 core is a single-pass parser over precompiled `struct` layouts
and integer bit operations, and streams files with constant memory, so
megabyte- to gigabyte-scale dumps are handled without surprises.

- **Phase 2 (v0.2):** `to_dataframe()` (optional `pandas` extra) and
  asyncio generators for live TCP socket / WebSocket streams
  (optional `websocket` extra).
- **Phase 3:** replace the core hot loop with Rust (PyO3), keeping the
  Python API unchanged.

## Development

```console
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest            # offline test suite
ruff check .      # lint
```

Tests marked `network` additionally parse public, real-world packet dumps
downloaded on the fly; they skip automatically when offline (CI runs them).

## Releasing (TestPyPI)

Publishing uses [trusted publishing] (no API tokens). One-time setup is
described in [`.github/workflows/publish-testpypi.yml`](.github/workflows/publish-testpypi.yml).
Then:

```console
git tag v0.1.0
git push origin v0.1.0
```

The workflow builds sdist + wheel, runs `twine check`, and uploads to
[TestPyPI].

[trusted publishing]: https://docs.pypi.org/trusted-publishers/
[TestPyPI]: https://test.pypi.org/project/fast-ccsds-parser/

## License

[MIT](LICENSE)

# fast-ccsds-parser

[![CI](https://github.com/daikiito-dk/fast-ccsds-parser/actions/workflows/ci.yml/badge.svg)](https://github.com/daikiito-dk/fast-ccsds-parser/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/fast-ccsds-parser)](https://pypi.org/project/fast-ccsds-parser/)

[Project page](https://daikiito-dk.github.io/fast-ccsds-parser/) · [日本語のドキュメントはこちら](README.ja.md)

Fast, dependency-free parsing of **CCSDS Space Packets** ([CCSDS 133.0-B-2])
for the modern Python data stack. Turn raw telemetry dumps into dicts and
structured objects in one pass: primary headers, packet boundaries, and
payloads, with a pure standard-library core that installs anywhere
(`pip install` and go).

Built for small-satellite teams, university labs, and data engineers who
just want their packets parsed, without pulling a decoding framework.

[CCSDS 133.0-B-2]: https://public.ccsds.org/Pubs/133x0b2e1.pdf

## Install

```console
pip install fast-ccsds-parser
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

## Related projects

Several mature Python libraries already handle CCSDS space packets, and all
of them can split packets and read primary headers without a mission packet
definition. Pick the one that fits your job:

| (as of 2026-10) | fast-ccsds-parser 0.1 | [ccsdspy] 2.0 | [space-packet-parser] 6.2 | [spacepackets] 0.32 |
| --- | --- | --- | --- | --- |
| Main focus | Structural parsing (headers, packet boundaries, payloads) | Field decoding from packet definitions | Field decoding from XTCE definitions | Building and parsing packets, incl. ECSS PUS and CFDP |
| Runtime dependencies | None | numpy, bitstruct, pyyaml, appdirs | click, lxml, rich | fastcrc, deprecation |
| Header-only parsing without definitions | Yes | Yes (`ccsdspy.utils.read_primary_headers`, `split_by_apid`) | Yes (`ccsds_generator`) | Yes (`parse_space_packets`) |
| Tabular output | Planned: Arrow / Polars / pandas | NumPy arrays (pandas via [SPaC-Kit]) | xarray (optional extra) | - |
| Live input | Planned: asyncio TCP / WebSocket | - | Sockets and UDP (synchronous) | Incremental parsing from a deque |
| Segmented packet reassembly | - | - | Yes | - |

If you need to *decode packet contents* against a mission packet database,
use [ccsdspy] or [space-packet-parser]; both are mature and used on flight
missions. If you build or parse ECSS PUS / CFDP traffic, use [spacepackets].
fast-ccsds-parser focuses on getting large packet dumps and live streams
into data-analysis tools quickly and with little memory.

[ccsdspy]: https://github.com/CCSDSPy/ccsdspy
[space-packet-parser]: https://github.com/lasp/space_packet_parser
[spacepackets]: https://github.com/ustgt-irs/spacepackets-py
[SPaC-Kit]: https://github.com/CCSDSPy/SPaC-Kit

## Performance

[`benchmarks/bench.py`](benchmarks/bench.py) measures packet throughput and
peak memory, running each case in a fresh process. Results for 1,000,000
synthetic 32-byte packets (32 MB), Python 3.12, Apple M4, ccsdspy 2.0.1,
space_packet_parser 6.2.0, pandas 3.0.6:

| Case | Time | Peak RSS |
| --- | ---: | ---: |
| fast-ccsds-parser: header walk | 0.47 s | 53 MB |
| fast-ccsds-parser: `iter_file` (streaming) | 0.83 s | 22 MB |
| fast-ccsds-parser: pandas via per-packet dicts | 2.37 s | 670 MB |
| ccsdspy: `read_primary_headers` | 21.28 s | 123 MB |
| ccsdspy: `iter_packet_bytes` (raw bytes only) | 0.41 s | 63 MB |
| space_packet_parser: `ccsds_generator` (raw bytes only) | 0.36 s | 72 MB |

What this shows:

- Streaming with `iter_file` uses the least memory of all cases.
- Building a DataFrame from per-packet dicts inflates memory about 20x over
  the input size. Columnar output (Phase 2a below) is designed to remove this.
- Iterating raw packet bytes is about 2x faster in ccsdspy and
  space_packet_parser, because `iter_file` also decodes every header and
  copies each payload. This is a known optimization target.

Run it yourself:

```console
pip install -e . ccsdspy space_packet_parser pandas
python benchmarks/bench.py                  # synthetic data
python benchmarks/bench.py path/to/dump.bin # your own dump
```

## Roadmap

- **Phase 2a:** columnar output (`to_arrow()`, `to_polars()`, `to_pandas()`)
  that keeps memory close to the input size, with Arrow / Polars / pandas as
  optional extras.
- **Phase 2b:** decoding CCSDS time codes (CUC / CDS) from secondary headers
  into timestamps.
- **Phase 2c:** asyncio parsing of live TCP socket / WebSocket streams
  (WebSocket as an optional extra).
- **Phase 3:** move the hot loops that benchmarks show are slow to Rust
  (PyO3), keeping the Python API unchanged.

## Development

```console
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest            # offline test suite
ruff check .      # lint
```

Tests marked `network` additionally parse public, real-world packet dumps
downloaded on the fly; they skip automatically when offline (CI runs them).

## Releasing (PyPI)

Publishing uses [trusted publishing] (no API tokens). One-time setup is
described in [`.github/workflows/publish-testpypi.yml`](.github/workflows/publish-testpypi.yml)
(the file name is historical; it publishes to PyPI). Bump `__version__` in
`src/fast_ccsds_parser/__init__.py`, add a CHANGELOG entry, then:

```console
git tag vX.Y.Z
git push origin vX.Y.Z
```

The workflow builds sdist + wheel, runs `twine check`, and uploads to
[PyPI].

[trusted publishing]: https://docs.pypi.org/trusted-publishers/
[PyPI]: https://pypi.org/project/fast-ccsds-parser/

## License

[MIT](LICENSE)

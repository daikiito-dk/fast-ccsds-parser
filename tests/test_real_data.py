"""Integration tests against a real-world spacecraft telemetry dump.

Fixture: SUrface Dust Analyzer (SUDA) instrument telemetry from NASA's
Europa Clipper mission, as published by the CCSDSPy project in the
SPaC-Kit repository (Apache-2.0). The dump is *downloaded at test time*
and not redistributed with this package; offline runs skip these tests.

Content is pinned by SHA-256: if the upstream file ever changes, the tests
skip (rather than fail) and the pin can be refreshed deliberately.
"""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

import pytest

from fast_ccsds_parser import iter_packets, iter_tables, parse_file, scan_file

pytestmark = pytest.mark.network

SUDA_URL = (
    "https://raw.githubusercontent.com/CCSDSPy/SPaC-Kit/main/data/"
    "suda_0409517390-0283787_cleansplit.dat"
)
SUDA_SHA256 = "de0767d9e7cbe33bfee85b7b75aeab3ab7c0cb7c2e5d5abebfadc76edd95b653"
# Known shape of the pinned dump: 7,289,344 bytes -> 10,589 packets.
SUDA_PACKET_COUNT = 10589
SUDA_APID_COUNTS = {
    1408: 155,
    1413: 2978,
    1414: 2978,
    1418: 2203,
    1419: 3,
    1424: 2270,
    1426: 2,
}


def _download_pinned(url: str, sha256: str) -> bytes:
    """Download ``url`` and verify its SHA-256; skip the test if impossible."""
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
    except OSError as exc:
        pytest.skip(f"cannot download fixture: {exc}")
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        pytest.skip(
            f"upstream fixture changed (sha256 {digest}, expected {sha256}); "
            "update the pinned fixture deliberately"
        )
    return data


@pytest.fixture(scope="module")
def suda_dump() -> bytes:
    return _download_pinned(SUDA_URL, SUDA_SHA256)


def test_real_dump_parses_completely(suda_dump: bytes) -> None:
    """The full 7 MB dump must parse as one clean packet stream."""
    packets = list(iter_packets(suda_dump))
    assert len(packets) == SUDA_PACKET_COUNT

    # Exact framing: reassembling the packet raws reproduces the dump
    # byte for byte (no gaps, no overlaps, no leftovers).
    assert b"".join(p.raw for p in packets) == suda_dump

    # Strict mode parsed every packet: all are version-0 space packets,
    # and every header field is within its CCSDS 133.0-B-2 range.
    assert all(p.header.version == 0 for p in packets)
    assert all(0 <= p.header.apid <= 0x7FF for p in packets)
    assert all(7 <= p.header.total_length <= 65542 for p in packets)
    assert all(0 <= p.header.sequence_count <= 0x3FFF for p in packets)


def test_real_dump_file_stream_matches_buffer(suda_dump: bytes, tmp_path: Path) -> None:
    """iter_file (streaming, O(1) memory) must agree with iter_packets."""
    path = tmp_path / "suda_cleansplit.dat"
    path.write_bytes(suda_dump)
    assert parse_file(path) == list(iter_packets(suda_dump))


def test_real_dump_apid_histogram(suda_dump: bytes) -> None:
    """Header extraction on real telemetry: APID histogram is stable."""
    counts: dict[int, int] = {}
    for packet in iter_packets(suda_dump):
        counts[packet.header.apid] = counts.get(packet.header.apid, 0) + 1
    assert counts == SUDA_APID_COUNTS


def test_real_dump_scan_file_matches_packets(suda_dump: bytes, tmp_path: Path) -> None:
    """Columnar scanning must agree with the per-packet parser on real data."""
    path = tmp_path / "suda_cleansplit.dat"
    path.write_bytes(suda_dump)
    table = scan_file(path)
    packets = list(iter_packets(suda_dump))
    assert len(table) == SUDA_PACKET_COUNT
    assert list(table.apid) == [p.header.apid for p in packets]
    assert list(table.sequence_count) == [p.header.sequence_count for p in packets]
    assert all(table.payload(i) == p.payload for i, p in enumerate(packets))

    chunks = list(iter_tables(path, chunk_packets=4000))
    assert [len(c) for c in chunks] == [4000, 4000, 2589]


def test_real_dump_to_arrow(suda_dump: bytes, tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    import pyarrow.compute as pc

    path = tmp_path / "suda_cleansplit.dat"
    path.write_bytes(suda_dump)
    arrow = scan_file(path).to_arrow()
    counts = pc.value_counts(arrow.column("apid")).to_pylist()
    assert {c["values"]: c["counts"] for c in counts} == SUDA_APID_COUNTS
    total = pc.sum(pc.binary_length(arrow.column("payload"))).as_py()
    assert total + 6 * SUDA_PACKET_COUNT == len(suda_dump)

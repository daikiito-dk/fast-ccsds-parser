"""Throughput and peak-memory benchmark for CCSDS space packet parsers.

Each case runs in a fresh subprocess so that peak RSS is measured per case.
Competitor libraries are optional; cases whose library is missing are
skipped.

Usage (from the repository root)::

    pip install -e . ccsdspy space_packet_parser pandas
    python benchmarks/bench.py                   # synthetic: 1,000,000 x 32-byte packets
    python benchmarks/bench.py path/to/dump.bin  # any file of concatenated packets

Peak RSS uses ``resource.getrusage``, so this script runs on Linux and macOS.
"""

from __future__ import annotations

import argparse
import logging
import resource
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _count_fcp_headers_only(path: str) -> int:
    from fast_ccsds_parser import PrimaryHeader

    data = Path(path).read_bytes()
    offset = count = 0
    while offset < len(data):
        offset += PrimaryHeader.from_bytes(data, offset=offset).total_length
        count += 1
    return count


def _count_fcp_iter_file(path: str) -> int:
    from fast_ccsds_parser import iter_file

    return sum(1 for _ in iter_file(path))


def _count_fcp_pandas_via_dicts(path: str) -> int:
    import pandas as pd

    from fast_ccsds_parser import iter_file

    return len(pd.DataFrame.from_records(p.to_dict() for p in iter_file(path)))


def _count_fcp_scan_file_headers(path: str) -> int:
    from fast_ccsds_parser import scan_file

    return len(scan_file(path, payload=False))


def _count_fcp_scan_file(path: str) -> int:
    from fast_ccsds_parser import scan_file

    return len(scan_file(path))


def _count_fcp_scan_to_polars(path: str) -> int:
    from fast_ccsds_parser import scan_file

    return len(scan_file(path).to_polars())


def _count_fcp_scan_to_pandas(path: str) -> int:
    from fast_ccsds_parser import scan_file

    return len(scan_file(path).to_pandas())


def _count_fcp_write_parquet(path: str) -> int:
    import tempfile

    from fast_ccsds_parser import write_parquet

    with tempfile.TemporaryDirectory() as tmp:
        return write_parquet(path, Path(tmp) / "out.parquet", chunk_packets=250_000)


def _count_ccsdspy_read_primary_headers(path: str) -> int:
    from ccsdspy.utils import read_primary_headers

    return len(read_primary_headers(path)["CCSDS_APID"])


def _count_ccsdspy_iter_packet_bytes(path: str) -> int:
    from ccsdspy.utils import iter_packet_bytes

    return sum(1 for _ in iter_packet_bytes(path))


def _count_spp_ccsds_generator(path: str) -> int:
    from space_packet_parser import ccsds_generator

    with open(path, "rb") as file:
        return sum(1 for _ in ccsds_generator(file))


CASES = {
    "fast-ccsds-parser: header walk": _count_fcp_headers_only,
    "fast-ccsds-parser: iter_file": _count_fcp_iter_file,
    "fast-ccsds-parser: pandas via dicts": _count_fcp_pandas_via_dicts,
    "fast-ccsds-parser: scan_file (no payload)": _count_fcp_scan_file_headers,
    "fast-ccsds-parser: scan_file": _count_fcp_scan_file,
    "fast-ccsds-parser: scan_file -> polars": _count_fcp_scan_to_polars,
    "fast-ccsds-parser: scan_file -> pandas": _count_fcp_scan_to_pandas,
    "fast-ccsds-parser: write_parquet": _count_fcp_write_parquet,
    "ccsdspy: read_primary_headers": _count_ccsdspy_read_primary_headers,
    "ccsdspy: iter_packet_bytes": _count_ccsdspy_iter_packet_bytes,
    "space_packet_parser: ccsds_generator": _count_spp_ccsds_generator,
}


def make_synthetic(path: Path, count: int, payload_size: int) -> None:
    """Write ``count`` back-to-back packets with ``payload_size``-byte data fields."""
    payload = bytes(payload_size)
    with open(path, "wb") as file:
        for i in range(count):
            word1 = 0x0100 | (i % 7)  # version 0, telemetry, 7 APIDs
            word2 = 0xC000 | (i & 0x3FFF)  # unsegmented, wrapping sequence count
            file.write(struct.pack(">HHH", word1, word2, payload_size - 1) + payload)


def run_case(name: str, path: str) -> None:
    """Child-process entry point: run one case and print a result line."""
    logging.disable(logging.CRITICAL)
    try:
        func = CASES[name]
        start = time.perf_counter()
        count = func(path)
        elapsed = time.perf_counter() - start
    except ImportError as exc:
        print(f"{name:44s} skipped ({exc.name} not installed)")
        return
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS but kilobytes on Linux.
    peak_mb = peak / 1024 / 1024 if sys.platform == "darwin" else peak / 1024
    print(f"{name:44s} {count:>10,d} pkts {elapsed:8.3f} s {peak_mb:8.1f} MB peak RSS")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", nargs="?", help="packet file (default: synthetic data)")
    parser.add_argument("--count", type=int, default=1_000_000, help="synthetic packet count")
    parser.add_argument("--payload", type=int, default=26, help="synthetic data field bytes")
    parser.add_argument("--case", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.case:
        run_case(args.case, args.file)
        return

    with tempfile.TemporaryDirectory() as tmp:
        if args.file:
            path = args.file
        else:
            path = str(Path(tmp) / "synthetic.bin")
            make_synthetic(Path(path), args.count, args.payload)
        size_mb = Path(path).stat().st_size / 1e6
        print(f"input: {path if args.file else 'synthetic'} ({size_mb:.1f} MB)")
        print(f"python {sys.version.split()[0]} on {sys.platform}\n")
        for name in CASES:
            subprocess.run(
                [sys.executable, __file__, path, "--case", name],
                check=True,
            )


if __name__ == "__main__":
    main()

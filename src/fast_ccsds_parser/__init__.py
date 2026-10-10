"""fast-ccsds-parser: fast, dependency-free parsing of CCSDS space packets.

Structural parsing of CCSDS 133.0-B-2 space packets with a pure standard
library core: primary header decoding, packet splitting, and payload
extraction from in-memory buffers and large dump files.

Example
-------
>>> from fast_ccsds_parser import parse_primary_header
>>> parse_primary_header(bytes.fromhex("0001c0000003"))
{'version': 0, 'type': 0, 'secondary_header_flag': 0, 'apid': 1,
'sequence_flags': 3, 'sequence_count': 0, 'packet_length': 3}
"""

from .exceptions import FastCCSDSError, InvalidPacketError, TruncatedPacketError
from .header import (
    CCSDS_PACKET_VERSION,
    IDLE_APID,
    PRIMARY_HEADER_SIZE,
    PacketType,
    PrimaryHeader,
    SequenceFlags,
    parse_primary_header,
)
from .packets import (
    SpacePacket,
    iter_file,
    iter_packets,
    parse_file,
    parse_packet,
    parse_packets,
)
from .table import PacketTable, iter_tables, scan_bytes, scan_file, write_parquet

__version__ = "0.2.0"

__all__ = [
    "__version__",
    # exceptions
    "FastCCSDSError",
    "InvalidPacketError",
    "TruncatedPacketError",
    # header
    "CCSDS_PACKET_VERSION",
    "IDLE_APID",
    "PRIMARY_HEADER_SIZE",
    "PacketType",
    "SequenceFlags",
    "PrimaryHeader",
    "parse_primary_header",
    # packets
    "SpacePacket",
    "parse_packet",
    "iter_packets",
    "parse_packets",
    "iter_file",
    "parse_file",
    # tables
    "PacketTable",
    "scan_bytes",
    "scan_file",
    "iter_tables",
    "write_parquet",
]

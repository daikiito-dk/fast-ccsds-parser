"""Quickstart: build synthetic packets, then parse them.

Run from the repository root:

    python examples/quickstart.py
"""

import struct

from fast_ccsds_parser import iter_packets, parse_primary_header


def build_packet(apid: int, sequence_count: int, payload: bytes) -> bytes:
    """Build one space packet: primary header + data field."""
    word1 = 0b000 << 13 | 0 << 12 | 0 << 11 | apid  # version|type|sec|APID
    word2 = 0b11 << 14 | sequence_count  # unsegmented
    word3 = len(payload) - 1  # data field length minus one
    return struct.pack(">HHH", word1, word2, word3) + payload


stream = b"".join(
    build_packet(apid=0x123, sequence_count=n, payload=bytes([n]) * 8)
    for n in range(5)
)

# 1) Just the first header, as a plain dict:
print(parse_primary_header(stream))

# 2) Every packet in a buffer, with header and payload separated:
for pkt in iter_packets(stream):
    row = pkt.to_dict()
    print(
        f"APID=0x{row['apid']:03X}  seq={row['sequence_count']}  "
        f"payload={row['payload'].hex()}"
    )

# 3) Large dumps: stream a file packet-by-packet without loading it:
#     from fast_ccsds_parser import iter_file
#     for pkt in iter_file("groundstation_dump.pkts"):
#         ...

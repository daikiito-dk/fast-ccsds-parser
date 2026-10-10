"""Columnar parsing of space packets into compact tables.

Instead of creating one Python object per packet, these functions keep the
parsed header fields in typed :class:`array.array` columns and the payloads
in one contiguous buffer. Memory stays close to the input size, and export
to Apache Arrow, Polars, and pandas reuses those buffers.

* :func:`scan_bytes` / :func:`scan_file` parse everything into one
  :class:`PacketTable`.
* :func:`iter_tables` yields one :class:`PacketTable` per chunk, for files
  larger than memory.
* :func:`write_parquet` streams a packet file into a Parquet file chunk by
  chunk.

The core needs only the standard library. ``to_arrow()`` and
:func:`write_parquet` need ``pyarrow`` (``pip install
fast-ccsds-parser[arrow]``); ``to_polars()`` and ``to_pandas()`` also need
``polars`` or ``pandas`` (the ``polars`` and ``pandas`` extras).
"""

from __future__ import annotations

import mmap
import os
import struct
import sys
from array import array
from collections.abc import Iterator
from functools import cached_property
from itertools import accumulate
from typing import TYPE_CHECKING, Any

from .exceptions import TruncatedPacketError
from .header import IDLE_APID, PRIMARY_HEADER_SIZE, PrimaryHeader

if TYPE_CHECKING:
    import pandas
    import polars
    import pyarrow

__all__ = [
    "PacketTable",
    "scan_bytes",
    "scan_file",
    "iter_tables",
    "write_parquet",
]

#: Default number of packets per chunk for :func:`iter_tables`.
DEFAULT_CHUNK_PACKETS = 1_000_000

# Word 1 (version/type/flag/APID) and word 3 (packet length) of a header.
_WALK_WORDS = struct.Struct(">H2xH")

# Packets per batch when gathering header and payload bytes. Bounds the
# number of temporary bytes objects alive at once.
_GATHER_BATCH = 65536

_TYPECODE_U16 = "H"
_TYPECODE_U64 = "Q"
_TYPECODE_I64 = "q"


class PacketTable:
    """Parsed space packets stored column by column.

    Header fields are exposed as :class:`array.array` columns with one
    entry per packet, in file order. Create instances with
    :func:`scan_bytes`, :func:`scan_file`, or :func:`iter_tables`.

    Columns
    -------
    ``offset``
        Byte position of each packet in the input (``uint64``).
    ``version``, ``type``, ``secondary_header_flag``, ``apid``,
    ``sequence_flags``, ``sequence_count``, ``packet_length``
        The primary header fields, as in :meth:`PrimaryHeader.to_dict`.
    ``payload``
        The packet data field (only if the table was scanned with
        ``payload=True``; read it with :meth:`payload`).
    """

    def __init__(
        self,
        offset: array,
        word1: array,
        word2: array,
        packet_length: array,
        payload_offsets: array | None,
        payload_data: bytearray | None,
    ) -> None:
        self.offset = offset
        self._word1 = word1
        self._word2 = word2
        self.packet_length = packet_length
        self._payload_offsets = payload_offsets
        self._payload_data = payload_data

    # -- basic protocol ---------------------------------------------------

    def __len__(self) -> int:
        return len(self.offset)

    def __repr__(self) -> str:
        payload = "with payload" if self.has_payload else "without payload"
        return f"<PacketTable: {len(self):,} packets, {payload}>"

    @property
    def has_payload(self) -> bool:
        """True if the table holds the packet payloads."""
        return self._payload_data is not None

    # -- header columns ---------------------------------------------------

    @cached_property
    def version(self) -> array:
        return array("B", [w >> 13 for w in self._word1])

    @cached_property
    def type(self) -> array:
        return array("B", [(w >> 12) & 1 for w in self._word1])

    @cached_property
    def secondary_header_flag(self) -> array:
        return array("B", [(w >> 11) & 1 for w in self._word1])

    @cached_property
    def apid(self) -> array:
        return array(_TYPECODE_U16, [w & 0x7FF for w in self._word1])

    @cached_property
    def sequence_flags(self) -> array:
        return array("B", [w >> 14 for w in self._word2])

    @cached_property
    def sequence_count(self) -> array:
        return array(_TYPECODE_U16, [w & 0x3FFF for w in self._word2])

    # -- payloads ---------------------------------------------------------

    def payload(self, index: int) -> bytes:
        """Return the payload (data field) of packet ``index``."""
        if self._payload_data is None or self._payload_offsets is None:
            raise ValueError("this table was scanned with payload=False")
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError("packet index out of range")
        start = self._payload_offsets[index]
        end = self._payload_offsets[index + 1]
        return bytes(self._payload_data[start:end])

    # -- export -----------------------------------------------------------

    def to_arrow(self) -> pyarrow.Table:
        """Return the packets as a :class:`pyarrow.Table`.

        Header columns reuse the table's buffers without copying. The
        ``payload`` column (``large_binary``) is included if the table
        was scanned with ``payload=True``.
        """
        pa, pc = _import_pyarrow()
        n = len(self)
        word1 = _arrow_view(pa, pa.uint16(), self._word1, n)
        word2 = _arrow_view(pa, pa.uint16(), self._word2, n)
        u8 = pa.uint8()
        u16 = pa.uint16()
        columns: dict[str, Any] = {
            "offset": _arrow_view(pa, pa.uint64(), self.offset, n),
            "version": pc.shift_right(word1, 13).cast(u8),
            "type": pc.bit_wise_and(pc.shift_right(word1, 12), 1).cast(u8),
            "secondary_header_flag": pc.bit_wise_and(pc.shift_right(word1, 11), 1).cast(u8),
            "apid": pc.bit_wise_and(word1, IDLE_APID).cast(u16),
            "sequence_flags": pc.shift_right(word2, 14).cast(u8),
            "sequence_count": pc.bit_wise_and(word2, 0x3FFF).cast(u16),
            "packet_length": _arrow_view(pa, u16, self.packet_length, n),
        }
        if self._payload_data is not None and self._payload_offsets is not None:
            columns["payload"] = pa.Array.from_buffers(
                pa.large_binary(),
                n,
                [
                    None,
                    pa.py_buffer(self._payload_offsets),
                    pa.py_buffer(self._payload_data),
                ],
            )
        return pa.table(columns, schema=_arrow_schema(pa, self.has_payload))

    def to_polars(self) -> polars.DataFrame:
        """Return the packets as a :class:`polars.DataFrame`."""
        try:
            import polars as pl
        except ImportError as exc:
            raise ImportError(
                "to_polars() needs polars and pyarrow: "
                "pip install 'fast-ccsds-parser[polars]'"
            ) from exc
        return pl.from_arrow(self.to_arrow())

    def to_pandas(self) -> pandas.DataFrame:
        """Return the packets as a :class:`pandas.DataFrame`.

        Header columns become NumPy-backed integer columns. The
        ``payload`` column stays Arrow-backed (``pd.ArrowDtype``), so no
        Python ``bytes`` object is created per packet.
        """
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError(
                "to_pandas() needs pandas and pyarrow: "
                "pip install 'fast-ccsds-parser[pandas]'"
            ) from exc
        pa, _ = _import_pyarrow()

        def types_mapper(arrow_type: Any) -> Any:
            if pa.types.is_large_binary(arrow_type):
                return pd.ArrowDtype(arrow_type)
            return None

        return self.to_arrow().to_pandas(types_mapper=types_mapper)


# -- scanning ---------------------------------------------------------------


def scan_bytes(
    data: bytes | bytearray | memoryview,
    *,
    payload: bool = True,
    strict: bool = True,
    skip_idle: bool = False,
) -> PacketTable:
    """Parse a buffer of concatenated packets into a :class:`PacketTable`.

    Parameters
    ----------
    data:
        Buffer containing whole packets.
    payload:
        Keep the packet payloads (default). Pass ``False`` to keep only the
        header columns, which needs much less memory.
    strict:
        Reject packets whose version is not 0.
    skip_idle:
        Leave out idle packets (APID = all ones).

    Raises
    ------
    InvalidPacketError
        If a packet fails the strict version check.
    TruncatedPacketError
        If the buffer ends with a partial header or a partial data field.
    """
    offsets, _ = _walk(data, 0, strict=strict, skip_idle=skip_idle, max_packets=None)
    return _build_table(data, offsets, payload=payload)


def scan_file(
    path: str | os.PathLike[str],
    *,
    payload: bool = True,
    strict: bool = True,
    skip_idle: bool = False,
) -> PacketTable:
    """Parse a whole packet file into one :class:`PacketTable`.

    The file is memory-mapped while scanning, so it is never loaded as one
    Python object; the resulting table holds only the columns (and the
    payloads, unless ``payload=False``). For files larger than memory,
    use :func:`iter_tables` or :func:`write_parquet`.

    See :func:`scan_bytes` for the parameters and errors.
    """
    tables = list(
        iter_tables(
            path,
            chunk_packets=None,
            payload=payload,
            strict=strict,
            skip_idle=skip_idle,
        )
    )
    return tables[0] if tables else _empty_table(payload)


def iter_tables(
    path: str | os.PathLike[str],
    *,
    chunk_packets: int | None = DEFAULT_CHUNK_PACKETS,
    payload: bool = True,
    strict: bool = True,
    skip_idle: bool = False,
) -> Iterator[PacketTable]:
    """Yield the packets of a file as consecutive :class:`PacketTable` chunks.

    Each chunk holds at most ``chunk_packets`` packets (``None`` means one
    chunk for the whole file). Only one chunk is held in memory at a time,
    so files larger than memory can be processed.

    See :func:`scan_bytes` for the other parameters and the errors.
    """
    if chunk_packets is not None and chunk_packets < 1:
        raise ValueError("chunk_packets must be a positive integer or None")
    with open(path, "rb") as file:
        if os.fstat(file.fileno()).st_size == 0:
            return
        with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as buffer:
            start = 0
            size = len(buffer)
            while start < size:
                offsets, start = _walk(
                    buffer,
                    start,
                    strict=strict,
                    skip_idle=skip_idle,
                    max_packets=chunk_packets,
                )
                if offsets:
                    yield _build_table(buffer, offsets, payload=payload)


def write_parquet(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    chunk_packets: int = DEFAULT_CHUNK_PACKETS,
    payload: bool = True,
    strict: bool = True,
    skip_idle: bool = False,
    compression: str = "zstd",
) -> int:
    """Convert a packet file to a Parquet file, one chunk at a time.

    Memory use is bounded by ``chunk_packets``, so this works for files
    larger than memory. Each chunk becomes one Parquet row group. Needs
    ``pyarrow`` (``pip install 'fast-ccsds-parser[arrow]'``).

    Returns
    -------
    int
        The number of packets written.
    """
    pa, _ = _import_pyarrow()
    import pyarrow.parquet as pq

    written = 0
    with pq.ParquetWriter(
        destination, _arrow_schema(pa, payload), compression=compression
    ) as writer:
        for table in iter_tables(
            source,
            chunk_packets=chunk_packets,
            payload=payload,
            strict=strict,
            skip_idle=skip_idle,
        ):
            writer.write_table(table.to_arrow())
            written += len(table)
    return written


# -- internals --------------------------------------------------------------


def _walk(
    buffer: Any,
    start: int,
    *,
    strict: bool,
    skip_idle: bool,
    max_packets: int | None,
) -> tuple[array, int]:
    """Find packet start offsets beginning at ``start``.

    Returns the offsets of the packets to keep and the position after the
    last packet examined. Stops after ``max_packets`` kept packets.
    """
    offsets = array(_TYPECODE_U64)
    append = offsets.append
    unpack = _WALK_WORDS.unpack_from
    size = len(buffer)
    limit = sys.maxsize if max_packets is None else max_packets
    position = start
    while position < size and len(offsets) < limit:
        if size - position < PRIMARY_HEADER_SIZE:
            raise TruncatedPacketError(
                f"need {PRIMARY_HEADER_SIZE} bytes for the primary header at "
                f"offset {position}, but only {size - position} byte(s) are available"
            )
        word1, length = unpack(buffer, position)
        end = position + length + 7
        if end > size:
            raise TruncatedPacketError(
                f"packet at offset {position} (APID {word1 & IDLE_APID}) declares "
                f"{length + 7} bytes, but only {size - position} remain"
            )
        if strict and word1 & 0xE000:
            # Raises InvalidPacketError with the standard message.
            PrimaryHeader.from_bytes(buffer, offset=position, strict=True)
        if not (skip_idle and word1 & IDLE_APID == IDLE_APID):
            append(position)
        position = end
    return offsets, position


def _build_table(buffer: Any, offsets: array, *, payload: bool) -> PacketTable:
    """Gather headers (and payloads) of the packets at ``offsets``."""
    headers = array(_TYPECODE_U16)
    payload_data = None
    for batch_start in range(0, len(offsets), _GATHER_BATCH):
        batch = offsets[batch_start : batch_start + _GATHER_BATCH]
        headers.frombytes(b"".join([buffer[o : o + PRIMARY_HEADER_SIZE] for o in batch]))
    if sys.byteorder == "little":
        headers.byteswap()
    word1 = headers[0::3]
    word2 = headers[1::3]
    lengths = headers[2::3]
    payload_offsets = None
    if payload:
        payload_offsets = array(
            _TYPECODE_I64, accumulate((n + 1 for n in lengths), initial=0)
        )
        # Allocate once and copy each payload straight from the source view,
        # so no temporary bytes objects are created. Growing a bytearray or
        # joining batches would briefly hold extra copies of the payloads.
        payload_data = bytearray(payload_offsets[-1])
        with memoryview(payload_data) as target, memoryview(buffer) as source:
            for o, start, end in zip(
                offsets, payload_offsets, payload_offsets[1:], strict=False
            ):
                data_start = o + PRIMARY_HEADER_SIZE
                target[start:end] = source[data_start : data_start + end - start]
    return PacketTable(offsets, word1, word2, lengths, payload_offsets, payload_data)


def _empty_table(payload: bool) -> PacketTable:
    return PacketTable(
        array(_TYPECODE_U64),
        array(_TYPECODE_U16),
        array(_TYPECODE_U16),
        array(_TYPECODE_U16),
        array(_TYPECODE_I64, [0]) if payload else None,
        bytearray() if payload else None,
    )


def _import_pyarrow() -> tuple[Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
    except ImportError as exc:
        raise ImportError(
            "Arrow export needs pyarrow: pip install 'fast-ccsds-parser[arrow]'"
        ) from exc
    return pa, pc


def _arrow_view(pa: Any, arrow_type: Any, values: array, length: int) -> Any:
    """Wrap a native-endian :class:`array.array` as an Arrow array without copying."""
    return pa.Array.from_buffers(arrow_type, length, [None, pa.py_buffer(values)])


def _arrow_schema(pa: Any, payload: bool) -> Any:
    fields = [
        ("offset", pa.uint64()),
        ("version", pa.uint8()),
        ("type", pa.uint8()),
        ("secondary_header_flag", pa.uint8()),
        ("apid", pa.uint16()),
        ("sequence_flags", pa.uint8()),
        ("sequence_count", pa.uint16()),
        ("packet_length", pa.uint16()),
    ]
    if payload:
        fields.append(("payload", pa.large_binary()))
    return pa.schema(fields)

"""Exception types raised while parsing CCSDS space packets."""


class FastCCSDSError(ValueError):
    """Base class for every exception raised by fast-ccsds-parser.

    Derives from :class:`ValueError` so that callers can also catch parsing
    errors with a plain ``except ValueError`` if they prefer.
    """


class InvalidPacketError(FastCCSDSError):
    """Raised when data does not look like a valid CCSDS space packet.

    Typical causes: a non-zero packet version number while parsing in strict
    mode, or trailing bytes behind a packet passed to :func:`parse_packet`.
    """


class TruncatedPacketError(FastCCSDSError):
    """Raised when a packet is cut short.

    Either the primary header is incomplete, or the data field is shorter
    than the ``packet_length`` field declares.
    """


class UnsupportedTimeCodeError(FastCCSDSError):
    """Raised for time codes outside the supported set.

    The supported codes are CUC and CDS (CCSDS 301.0-B-4), including the
    two-octet CUC P-field extension. CCS, agency-defined level 3/4 codes,
    reserved time code identifications, and reserved field values raise
    this error.
    """

"""Announce the Sunshine on this device over mDNS when Sunshine cannot.

Moonlight finds hosts by browsing ``_nvstream._tcp.local``. Sunshine publishes
that service through avahi-daemon, which SteamOS may not allow for user
services ("avahi::entry_group_new() failed: Not permitted" in Sunshine's log),
so other devices never see this one. When that error shows up, Eclipse answers
the mDNS queries itself: a small responder (RFC 6762/6763) with the same
records Sunshine would publish, under a host name of its own so it never
conflicts with avahi's records for this device.

Standard library only; the message encoding is pure functions for the tests.
"""

from __future__ import annotations

import asyncio
import re
import socket
import struct
from dataclasses import dataclass, field
from typing import Any, Optional

from .log import get_logger

GROUP = "224.0.0.251"
PORT = 5353
SERVICE = "_nvstream._tcp.local"
SERVICES_META = "_services._dns-sd._udp.local"

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_SRV = 33
TYPE_ANY = 255
CLASS_IN = 1
# Top bit of the class: "cache flush" in answers, "unicast response wanted" in questions.
CLASS_TOP_BIT = 0x8000

TTL_HOST = 120
TTL_SERVICE = 4500
# Unsolicited announcements at start (RFC 6762 §8.3: at least two, a second apart).
ANNOUNCE_COUNT = 3
ANNOUNCE_INTERVAL_S = 1.0

# Sunshine's log lines when publishing through avahi fails.
_PUBLISH_FAILED_RE = re.compile(
    r"avahi::entry_group_new\(\) failed|Avahi entry group failure|"
    r"Failed to add _nvstream\._tcp service|Failed to commit entry group|"
    r"Failed to create client: |Client failure: "
)
_STARTUP_RE = re.compile(r"Sunshine version:", re.IGNORECASE)


def publish_failed(log_text: str) -> bool:
    """Whether Sunshine's latest run (the log after its last startup line) failed to publish over mDNS."""
    start = 0
    for match in _STARTUP_RE.finditer(log_text):
        start = match.start()
    return bool(_PUBLISH_FAILED_RE.search(log_text, start))


def dns_label(text: str, fallback: str = "device") -> str:
    """A host name label: letters, digits and dashes, at most 63 characters."""
    label = re.sub(r"-{2,}", "-", re.sub(r"[^A-Za-z0-9-]+", "-", text)).strip("-")[:63].strip("-")
    return label or fallback


def instance_label(text: str) -> str:
    """A service instance label: any text, at most 63 bytes of UTF-8."""
    # A dot would split the name into DNS labels.
    data = (text.replace(".", " ").strip() or "Sunshine").encode("utf-8")[:63]
    return data.decode("utf-8", errors="ignore").strip() or "Sunshine"


# ---------------------------------------------------------------- encoding


def encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        data = label.encode("utf-8")
        if not data or len(data) > 63:
            raise ValueError(f"Invalid DNS label in {name!r}")
        out += bytes([len(data)]) + data
    return out + b"\x00"


def read_name(data: bytes, offset: int) -> tuple[str, int]:
    """Name at ``offset`` (following compression pointers) and the offset after it."""
    labels: list[str] = []
    end: Optional[int] = None
    jumps = 0
    while True:
        if offset >= len(data):
            raise ValueError("Name runs past the message")
        length = data[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("Truncated pointer")
            if end is None:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | data[offset + 1]
            jumps += 1
            if jumps > 32:
                raise ValueError("Pointer loop")
            continue
        if length & 0xC0:
            raise ValueError("Unsupported label type")
        offset += 1
        if length == 0:
            break
        labels.append(data[offset:offset + length].decode("utf-8", errors="replace"))
        offset += length
    return ".".join(labels), end if end is not None else offset


@dataclass
class Question:
    name: str
    qtype: int
    unicast: bool


@dataclass
class Query:
    id: int
    questions: list[Question] = field(default_factory=list)
    # Raw question section, echoed in answers to legacy (non-5353) queriers.
    raw_questions: bytes = b""


def parse_query(data: bytes) -> Optional[Query]:
    """The questions of an mDNS query, or None for responses and malformed packets."""
    if len(data) < 12:
        return None
    msg_id, flags, qdcount = struct.unpack("!HHH", data[:6])
    if flags & 0x8000:
        return None
    query = Query(msg_id)
    offset = 12
    try:
        for _ in range(qdcount):
            name, offset = read_name(data, offset)
            if offset + 4 > len(data):
                return None
            qtype, qclass = struct.unpack("!HH", data[offset:offset + 4])
            offset += 4
            query.questions.append(Question(name.lower(), qtype, bool(qclass & CLASS_TOP_BIT)))
    except ValueError:
        return None
    query.raw_questions = data[12:offset]
    return query


@dataclass(frozen=True)
class Record:
    name: str
    rtype: int
    ttl: int
    rdata: bytes
    flush: bool

    def encode(self, ttl: Optional[int] = None) -> bytes:
        rclass = CLASS_IN | (CLASS_TOP_BIT if self.flush else 0)
        ttl_value = self.ttl if ttl is None else ttl
        return encode_name(self.name) + struct.pack("!HHIH", self.rtype, rclass, ttl_value, len(self.rdata)) + self.rdata


@dataclass(frozen=True)
class Service:
    """What is announced: Sunshine's service on ``port`` of ``host`` at ``addresses``."""

    instance: str
    host: str
    port: int
    addresses: tuple[str, ...]

    @property
    def instance_name(self) -> str:
        return f"{self.instance}.{SERVICE}"

    @property
    def host_name(self) -> str:
        return f"{self.host}.local"

    def records(self) -> dict[str, list[Record]]:
        """Records by role: "ptr", "srv", "txt", "a", "meta"."""
        srv = struct.pack("!HHH", 0, 0, self.port) + encode_name(self.host_name)
        return {
            "meta": [Record(SERVICES_META, TYPE_PTR, TTL_SERVICE, encode_name(SERVICE), False)],
            "ptr": [Record(SERVICE, TYPE_PTR, TTL_SERVICE, encode_name(self.instance_name), False)],
            "srv": [Record(self.instance_name, TYPE_SRV, TTL_HOST, srv, True)],
            # An empty TXT record holds one empty string.
            "txt": [Record(self.instance_name, TYPE_TXT, TTL_SERVICE, b"\x00", True)],
            "a": [Record(self.host_name, TYPE_A, TTL_HOST, socket.inet_aton(a), True) for a in self.addresses],
        }


def answer(query: Query, service: Service) -> tuple[list[Record], list[Record]]:
    """Answers and additional records for ``query``; both empty when it is not about ``service``."""
    recs = service.records()
    answers: list[Record] = []
    additional: list[Record] = []

    def add(target: list[Record], items: list[Record]) -> None:
        for item in items:
            if item not in answers and item not in additional:
                target.append(item)

    instance = service.instance_name.lower()
    host = service.host_name.lower()
    for q in query.questions:
        wants = lambda *types: q.qtype in (*types, TYPE_ANY)  # noqa: E731
        if q.name == SERVICES_META and wants(TYPE_PTR):
            add(answers, recs["meta"])
        elif q.name == SERVICE.lower() and wants(TYPE_PTR):
            add(answers, recs["ptr"])
            add(additional, recs["srv"] + recs["txt"] + recs["a"])
        elif q.name == instance:
            if wants(TYPE_SRV):
                add(answers, recs["srv"])
                add(additional, recs["a"])
            if wants(TYPE_TXT):
                add(answers, recs["txt"])
        elif q.name == host and wants(TYPE_A):
            add(answers, recs["a"])
    # Additional records the answers already hold are not repeated.
    return answers, [r for r in additional if r not in answers]


def build_response(answers: list[Record], additional: list[Record], query: Optional[Query] = None,
                   ttl: Optional[int] = None) -> bytes:
    """An mDNS response. With a legacy ``query``, its id and questions are echoed (RFC 6762 §6.7)."""
    msg_id = query.id if query else 0
    qdcount = len(query.questions) if query else 0
    header = struct.pack("!HHHHHH", msg_id, 0x8400, qdcount, len(answers), 0, len(additional))
    body = (query.raw_questions if query else b"")
    body += b"".join(r.encode(ttl) for r in answers) + b"".join(r.encode(ttl) for r in additional)
    return header + body


def announcement(service: Service, goodbye: bool = False) -> bytes:
    """Unsolicited response with every record; a goodbye (TTL 0) withdraws them."""
    recs = service.records()
    answers = recs["ptr"] + recs["srv"] + recs["txt"] + recs["a"]
    return build_response(answers, [], ttl=0 if goodbye else None)


# ---------------------------------------------------------------- responder


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, responder: "Responder") -> None:
        self.responder = responder

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.responder.handle(data, addr)

    def error_received(self, exc: Exception) -> None:
        get_logger().debug("mDNS socket error: %s", exc)


class Responder:
    """Answers mDNS queries for one service on the given interface addresses."""

    def __init__(self, service: Service) -> None:
        self.service = service
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._sock: Optional[socket.socket] = None
        self._announcer: Optional[asyncio.Task[Any]] = None

    @property
    def running(self) -> bool:
        return self._transport is not None

    def _open_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            # avahi-daemon may hold the port too: both receive the multicast queries.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.bind(("", PORT))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            joined = 0
            for address in self.service.addresses:
                mreq = socket.inet_aton(GROUP) + socket.inet_aton(address)
                try:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                    joined += 1
                except OSError as exc:
                    get_logger().warning("mDNS: cannot listen on %s: %s", address, exc)
            if not joined:
                raise OSError("no interface could join the mDNS group")
            sock.setblocking(False)
        except BaseException:
            sock.close()
            raise
        return sock

    async def start(self) -> None:
        if self._transport:
            return
        loop = asyncio.get_running_loop()
        sock = self._open_socket()
        try:
            transport, _ = await loop.create_datagram_endpoint(lambda: _Protocol(self), sock=sock)
        except BaseException:
            sock.close()
            raise
        self._sock = sock
        self._transport = transport  # type: ignore[assignment]
        self._announcer = asyncio.ensure_future(self._announce())
        get_logger().info(
            "mDNS: announcing %s on %s", self.service.instance_name, ", ".join(self.service.addresses)
        )

    async def _announce(self) -> None:
        for index in range(ANNOUNCE_COUNT):
            self._send_multicast(announcement(self.service))
            if index + 1 < ANNOUNCE_COUNT:
                await asyncio.sleep(ANNOUNCE_INTERVAL_S * (2 ** index))

    def _send_multicast(self, payload: bytes) -> None:
        if not self._transport or not self._sock:
            return
        for address in self.service.addresses:
            try:
                # Out of every interface, so each network hears about this device.
                self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
                self._transport.sendto(payload, (GROUP, PORT))
            except OSError as exc:
                get_logger().debug("mDNS: sending on %s failed: %s", address, exc)

    def handle(self, data: bytes, addr: tuple[str, int]) -> None:
        query = parse_query(data)
        if not query or not query.questions:
            return
        answers, additional = answer(query, self.service)
        if not answers or not self._transport:
            return
        legacy = addr[1] != PORT
        if legacy:
            # A plain DNS resolver asking on 5353: answer it directly, like a DNS server,
            # without the mDNS cache-flush bit (RFC 6762 §6.7).
            plain = lambda records: [Record(r.name, r.rtype, r.ttl, r.rdata, False) for r in records]  # noqa: E731
            self._transport.sendto(build_response(plain(answers), plain(additional), query, ttl=10), addr)
        elif all(q.unicast for q in query.questions):
            self._transport.sendto(build_response(answers, additional), addr)
        else:
            self._send_multicast(build_response(answers, additional))

    async def stop(self) -> None:
        if self._announcer:
            self._announcer.cancel()
            self._announcer = None
        if self._transport:
            self._send_multicast(announcement(self.service, goodbye=True))
            self._transport.close()
        self._transport = None
        self._sock = None

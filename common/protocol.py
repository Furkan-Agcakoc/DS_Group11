"""Message format shared by clients and servers.

Every message is a JSON object with at least:
    type    - one of the constants below
    sender  - UUID of the sending node (server UUID or player ID)
    msg_id  - unique ID, used to drop duplicates

TCP has no message boundaries, so each message is sent as a 4-byte
big-endian length prefix followed by the UTF-8 encoded JSON body.
UDP datagrams keep their boundaries and carry the plain JSON body.
"""

import json
import socket
import struct
import uuid

from common import config

# --- Message types (see the message table in the project report) -------------------
# client <-> server
CREATE_ROOM = "CREATE_ROOM"       # client -> leader
ROOM_CREATED = "ROOM_CREATED"
JOIN = "JOIN"
REJOIN = "REJOIN"                 # M4
JOINED = "JOINED"
ROOM_UPDATE = "ROOM_UPDATE"
START_GAME = "START_GAME"
ROUND_START = "ROUND_START"
GUESS = "GUESS"
FEEDBACK = "FEEDBACK"
ROUND_RESULT = "ROUND_RESULT"
GAME_OVER = "GAME_OVER"
PING = "PING"
PONG = "PONG"
LEAVE = "LEAVE"
KICKED = "KICKED"                 # host -> client: removed for inactivity (no auto-rejoin)
ERROR = "ERROR"

# discovery (UDP)
HELLO = "HELLO"                   # new server -> broadcast
DISCOVER_GAME = "DISCOVER_GAME"   # client -> broadcast
GAME_LIST = "GAME_LIST"           # leader -> client (unicast)

# server <-> server (TCP)
VIEW_UPDATE = "VIEW_UPDATE"       # leader -> all servers: group view + room table
HOST_ROOM = "HOST_ROOM"           # leader -> chosen host: create this room
ROOM_STATUS = "ROOM_STATUS"       # host -> leader: player count / state changed
ELECTION = "ELECTION"
HEARTBEAT = "HEARTBEAT"
STATE_UPDATE = "STATE_UPDATE"     # room host -> backup: full room state
ACK = "ACK"
PROMOTE = "PROMOTE"               # leader -> backup: take over the room of a crashed host

_HEADER = struct.Struct("!I")
_MAX_MESSAGE = 1 << 20  # 1 MiB, protects against garbage length prefixes


def new_id():
    return str(uuid.uuid4())


def make_msg(msg_type, sender, **fields):
    msg = {"type": msg_type, "sender": sender, "msg_id": new_id()}
    msg.update(fields)
    return msg


def encode(msg):
    return json.dumps(msg).encode("utf-8")


def decode(data):
    return json.loads(data.decode("utf-8"))


def send_msg(sock, msg):
    """Send one length-prefixed message over a TCP socket."""
    body = encode(msg)
    sock.sendall(_HEADER.pack(len(body)) + body)


def recv_msg(sock):
    """Receive one message from a TCP socket. Returns None when the peer closed the connection."""
    header = _recv_exact(sock, _HEADER.size)
    if header is None:
        return None
    (length,) = _HEADER.unpack(header)
    if length > _MAX_MESSAGE:
        raise ValueError(f"message too large: {length} bytes")
    body = _recv_exact(sock, length)
    if body is None:
        return None
    return decode(body)


def request(address, msg, expect_reply=False, timeout=None):
    """Send one message over a short-lived TCP connection, optionally wait for one reply.

    Raises OSError if the peer is unreachable; returns the reply (or None).
    """
    with socket.create_connection(address, timeout=timeout or config.SERVER_TIMEOUT) as s:
        send_msg(s, msg)
        return recv_msg(s) if expect_reply else None


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def local_ip():
    """IP address of the interface used for the LAN (not 127.0.0.1).

    Connecting a UDP socket sends no packet; it only makes the OS pick the outgoing interface.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()

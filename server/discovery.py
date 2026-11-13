"""Dynamic discovery via UDP broadcast.

Server discovers servers:
    A starting server broadcasts HELLO (UUID, IP, TCP port) every HELLO_INTERVAL. The leader adds it
    to the group view and sends the new view to all servers (VIEW_UPDATE over TCP); no election is
    started, so running games are not disturbed. If no leader answers within DISCOVERY_TIMEOUT
    (system start), the server builds the view from the HELLOs it heard from other starting
    servers and starts an LCR election. A starting server that receives an ELECTION message
    before its own timeout joins that election with the servers it has heard so far.

Client discovers server:
    A client broadcasts DISCOVER_GAME; only the leader replies (UDP unicast) with GAME_LIST:
    the open rooms and the address of each room's host. No IP has to be configured.
"""

import logging
import socket
import threading
import time

from common import config, protocol as p

log = logging.getLogger("discovery")


class Discovery:
    def __init__(self, server, broadcast_addr):
        self.server = server
        self.broadcast_addr = broadcast_addr
        self.sock = None
        self.candidates = {}   # servers heard via HELLO while there is no leader: uuid -> (ip, port)
        self.lock = threading.Lock()

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Options must be set before bind(). They let several servers on one machine share the
        # broadcast port (for local tests): SO_REUSEADDR on Windows, SO_REUSEPORT on macOS/Linux.
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("", config.BROADCAST_PORT))
        threading.Thread(target=self._listen, name="udp-listener", daemon=True).start()

    # --- Joining the server group ---------------------------------------------------------
    def join_group(self):
        """Broadcast HELLO until the leader's VIEW_UPDATE arrives; start the group if nobody answers."""
        s = self.server
        hello = p.make_msg(p.HELLO, s.server_id, ip=s.ip, port=s.port)
        deadline = time.monotonic() + config.DISCOVERY_TIMEOUT
        while time.monotonic() < deadline:
            self._broadcast(hello)
            if s.joined_group.wait(config.HELLO_INTERVAL):
                if s.view.leader:
                    log.info("joined group, leader is %s", s.view.leader[:8])
                else:
                    log.info("joined the election of other starting servers")
                return
        if self.form_group():
            log.info("no leader answered within %.0f s -> starting an election",
                     config.DISCOVERY_TIMEOUT)
            s.election.start("system start")

    def form_group(self):
        """Build the initial view from this server and the starting servers heard so far."""
        s = self.server
        with self.lock:
            if s.joined_group.is_set():
                return False
            s.view.add_member(s.server_id, s.ip, s.port)
            for uuid, (ip, port) in self.candidates.items():
                s.view.add_member(uuid, ip, port)
            s.joined_group.set()
            return True

    def _broadcast(self, msg):
        try:
            self.sock.sendto(p.encode(msg), (self.broadcast_addr, config.BROADCAST_PORT))
        except OSError as e:
            log.warning("broadcast failed: %s", e)

    # --- Incoming broadcasts -------------------------------------------------------------
    def _listen(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(65535)
                msg = p.decode(data)
            except (OSError, ValueError):
                continue
            if msg.get("sender") == self.server.server_id:
                continue  # our own broadcast
            if msg.get("type") == p.HELLO and self.server.view.leader is None:
                with self.lock:  # system start: remember the other starting servers
                    self.candidates[msg["sender"]] = (msg["ip"], msg["port"])
                continue
            if not self.server.is_leader:
                continue  # only the leader answers discovery requests
            if msg.get("type") == p.HELLO:
                self._on_hello(msg)
            elif msg.get("type") == p.DISCOVER_GAME:
                self._on_discover_game(msg, addr)

    def _on_hello(self, msg):
        s = self.server
        changed = s.view.add_member(msg["sender"], msg["ip"], msg["port"])
        if changed:
            log.info("server %s joined from %s:%d (view v%d)",
                     msg["sender"][:8], msg["ip"], msg["port"], s.view.version)
            s.replication.repair_rooms()  # rooms without a backup can get one now
        # Send the view even if nothing changed: the new server may have missed the last update.
        s.push_view()

    def _on_discover_game(self, msg, addr):
        s = self.server
        reply = p.make_msg(p.GAME_LIST, s.server_id, leader={"ip": s.ip, "port": s.port},
                           rooms=s.view.public_rooms())
        try:
            self.sock.sendto(p.encode(reply), addr)
        except OSError as e:
            log.warning("GAME_LIST to %s failed: %s", addr, e)

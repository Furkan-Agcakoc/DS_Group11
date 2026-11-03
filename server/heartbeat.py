"""Failure detection with heartbeats (UDP unicast).

Every HEARTBEAT_INTERVAL the leader sends a heartbeat to every server, and every server sends one
to the leader. Each server listens for heartbeats on UDP at the same port number as its TCP port.

A node counts as crashed after HEARTBEAT_MISSES missed heartbeats:
  - the leader removes a silent server from the group view and sends the new view
    (M4: and promotes the backups of the rooms that server hosted);
  - a server that no longer hears the leader removes it and starts an LCR election.

A server the leader does not know (e.g. removed after a short network problem) is added again
when its heartbeats arrive, because heartbeats carry the sender's address.
"""

import logging
import socket
import threading
import time

from common import config, protocol as p

log = logging.getLogger("heartbeat")

ELECTION_TIMEOUT = 3 * config.HEARTBEAT_MISSES * config.HEARTBEAT_INTERVAL


class Heartbeat:
    def __init__(self, server):
        self.server = server
        self.last_seen = {}       # uuid -> time of the last heartbeat
        self.lock = threading.Lock()
        self.sock = None

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", self.server.port))
        threading.Thread(target=self._listen, name="hb-listener", daemon=True).start()
        threading.Thread(target=self._send_loop, name="hb-sender", daemon=True).start()
        threading.Thread(target=self._monitor_loop, name="hb-monitor", daemon=True).start()

    def seen(self, uuid):
        with self.lock:
            self.last_seen[uuid] = time.monotonic()

    def reset(self):
        """After an election everyone gets a fresh grace period of HEARTBEAT_MISSES intervals."""
        with self.lock:
            self.last_seen.clear()

    # --- Sending -------------------------------------------------------------------------
    def _send_loop(self):
        s = self.server
        while True:
            time.sleep(config.HEARTBEAT_INTERVAL)
            view = s.view
            if view.leader is None:
                continue  # election running
            if s.is_leader:
                targets = view.others(s.server_id)
            else:
                leader = view.members.get(view.leader)
                targets = [leader] if leader else []
            msg = p.encode(p.make_msg(p.HEARTBEAT, s.server_id, ip=s.ip, port=s.port,
                                      leader=view.leader))
            for m in targets:
                try:
                    self.sock.sendto(msg, (m["ip"], m["port"]))
                except OSError:
                    pass  # omission: a missing heartbeat is exactly what the monitor detects

    # --- Receiving -----------------------------------------------------------------------
    def _listen(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(65535)
                msg = p.decode(data)
            except (OSError, ValueError):
                continue  # Windows reports ICMP "port unreachable" of earlier sends here
            if msg.get("type") != p.HEARTBEAT:
                continue
            self.seen(msg["sender"])
            s = self.server
            if s.is_leader and msg["sender"] not in s.view.members:
                log.info("heartbeat from unknown server %s -> adding it again", msg["sender"][:8])
                s.view.add_member(msg["sender"], msg["ip"], msg["port"])
                s.push_view()

    # --- Failure detection ----------------------------------------------------------------
    def _monitor_loop(self):
        s = self.server
        timeout = config.HEARTBEAT_MISSES * config.HEARTBEAT_INTERVAL
        while True:
            time.sleep(config.HEARTBEAT_INTERVAL)
            if not s.joined_group.is_set():
                continue
            if s.view.leader is None:
                s.election.restart_if_stuck(ELECTION_TIMEOUT)
                continue
            now = time.monotonic()
            if s.is_leader:
                watched = [m["uuid"] for m in s.view.others(s.server_id)]
            else:
                watched = [s.view.leader]
            for uuid in watched:
                with self.lock:
                    last = self.last_seen.setdefault(uuid, now)  # start watching now
                if now - last > timeout:
                    with self.lock:
                        self.last_seen.pop(uuid, None)
                    log.warning("no heartbeat from %s for %.0f s -> crashed", uuid[:8], now - last)
                    if s.is_leader:
                        s.on_server_failed(uuid)
                    else:
                        s.on_leader_failed(uuid)
                        break

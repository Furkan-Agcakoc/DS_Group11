"""Leader election with the LeLann-Chang-Roberts (LCR) algorithm.

Ring: the UUIDs of the group view, sorted; messages always go to the next UUID (the successor).
Message: ELECTION {"mid": UUID, "isLeader": bool}, sent over TCP.

Rules (as in the lecture):
  start:                         send E(own, false), become participant
  receive E(mid, false):
    mid > own                    forward E(mid, false), become participant
    mid < own, not participant   send E(own, false) instead, become participant
    mid < own, participant       discard (our own, larger ID is already on its way)
    mid == own                   own ID went around the ring: we are the leader;
                                 send E(own, true), become non-participant
  receive E(mid, true):
    mid != own                   note the leader, forward, become non-participant
    mid == own                   announcement went around the ring: election finished

UUIDs are compared as strings; all UUID strings have the same format, so the order is well defined.
Elections start at system start and when the leader fails (missed heartbeats). An unreachable
successor is skipped. If an election does not finish within ELECTION_TIMEOUT, it is restarted.
"""

import logging
import threading
import time

from common import protocol as p

log = logging.getLogger("election")


class Election:
    def __init__(self, server):
        self.server = server
        self.participant = False
        self.started_at = None    # time of the last start, for the restart timeout
        self.lock = threading.RLock()

    @property
    def own(self):
        return self.server.server_id

    def start(self, reason):
        with self.lock:
            log.info("ELECTION started (%s), ring: %s", reason,
                     " -> ".join(u[:8] for u in self.server.view.ring()))
            self.participant = True
            self.started_at = time.monotonic()
            self._send(self.own, False)

    def on_message(self, msg):
        mid, is_leader = msg["mid"], msg["isLeader"]
        with self.lock:
            if is_leader:
                self.participant = False
                self.started_at = None
                if mid == self.own:
                    log.info("ELECTION finished: announcement went around the ring")
                    return
                log.info("new leader is %s", mid[:8])
                self.server.on_leader_elected(mid)
                self._send(mid, True)
            elif mid == self.own:
                log.info("own ID went around the ring -> I am the LEADER")
                self.participant = False
                self.started_at = None
                self.server.on_elected()
                self._send(self.own, True)
            elif mid > self.own:
                self.participant = True
                self._send(mid, False)
            elif not self.participant:
                self.participant = True
                self.started_at = self.started_at or time.monotonic()
                self._send(self.own, False)
            # else: mid < own and already participant -> discard

    def _send(self, mid, is_leader):
        """Send to the successor; skip successors that cannot be reached."""
        msg = p.make_msg(p.ELECTION, self.own, mid=mid, isLeader=is_leader)
        view = self.server.view
        for uuid in view.successors(self.own):
            address = view.address(uuid)
            if address is None:
                continue
            try:
                p.request(address, msg)
                return
            except OSError as e:
                log.warning("successor %s unreachable (%s), skipping it", uuid[:8], e)
        # No other server reachable: the ring consists of this server alone.
        if mid == self.own:
            self.on_message(msg)
        elif not is_leader:
            log.warning("no other server reachable -> electing myself")
            self.on_message(p.make_msg(p.ELECTION, self.own, mid=self.own, isLeader=False))

    def restart_if_stuck(self, timeout):
        """Called by the heartbeat monitor: restart an election that did not finish in time."""
        with self.lock:
            stuck = self.started_at is not None and time.monotonic() - self.started_at > timeout
        if stuck:
            self.start("previous election timed out")

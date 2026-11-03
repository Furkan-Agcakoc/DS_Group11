"""Primary-backup replication per room and failover.

Every room has a host (primary) and a backup: the host's successor in the ring, chosen by the
leader and stored in the room table.

Replication (host -> backup):
    After every change (join, guess, round end ...) the host sends STATE_UPDATE with the full room
    state and waits for the ACK before it answers the player. So every guess a player got
    feedback for is also stored on the backup. If no ACK arrives within REPLICATION_TIMEOUT, the
    update is retried REPLICATION_RETRIES times; then the backup is reported as failed and the
    game continues without a backup until the leader assigns a new one.

Failover (leader):
    When a server is removed from the view (missed heartbeats) or a new leader is elected, the
    leader repairs the room table: a room whose host is gone is taken over by its backup
    (PROMOTE), and every room whose backup is gone gets a new one. Rooms whose host and backup
    crashed at the same time are lost.

The promoted backup marks all players as disconnected; they rediscover the room's new host and
REJOIN with their player ID within REJOIN_GRACE seconds.
"""

import logging
import threading

from common import config, protocol as p
from server.game import FINISHED, Room

log = logging.getLogger("replication")


class Replication:
    def __init__(self, server):
        self.server = server
        self.replicas = {}        # room_id -> latest state received as backup
        self.lock = threading.Lock()
        self._known_backup = {}   # room_id -> backup we last sent the full state to

    # --- Host side ------------------------------------------------------------------------
    def replicate(self, room):
        """Send the room state to its backup and wait for the ACK. Call with room.lock held."""
        s = self.server
        entry = s.view.rooms.get(room.id)
        backup = entry.get("backup") if entry else None
        if not backup or backup == s.server_id:
            return False
        address = s.view.address(backup)
        if address is None:
            return False
        msg = p.make_msg(p.STATE_UPDATE, s.server_id, room=room.to_dict())
        for attempt in range(1, config.REPLICATION_RETRIES + 1):
            try:
                reply = p.request(address, msg, expect_reply=True,
                                  timeout=config.REPLICATION_TIMEOUT)
                if reply and reply["type"] == p.ACK:
                    self._known_backup[room.id] = backup
                    return True
            except (OSError, ValueError) as e:
                log.warning("STATE_UPDATE %s to backup %s failed (attempt %d/%d): %s", room.id,
                            backup[:8], attempt, config.REPLICATION_RETRIES, e)
        log.error("backup %s of room %s FAILED -> continuing without backup until the leader "
                  "assigns a new one", backup[:8], room.id)
        return False

    def sync_backups(self):
        """After a view change: send the full state to every newly assigned backup."""
        s = self.server
        with s.rooms_lock:
            rooms = list(s.rooms.values())
        for room in rooms:
            entry = s.view.rooms.get(room.id)
            backup = entry.get("backup") if entry else None
            if entry and entry["host"] == s.server_id and backup \
                    and self._known_backup.get(room.id) != backup:
                self._known_backup[room.id] = backup
                threading.Thread(target=self._replicate_locked, args=(room,),
                                 name=f"sync-{room.id}", daemon=True).start()

    def _replicate_locked(self, room):
        with room.lock:
            if self.replicate(room):
                log.info("room %s: full state sent to new backup %s", room.id,
                         self._known_backup[room.id][:8])

    # --- Backup side ----------------------------------------------------------------------
    def on_state_update(self, session, msg):
        room = msg["room"]
        with self.lock:
            self.replicas[room["id"]] = room
        session.send(p.make_msg(p.ACK, self.server.server_id, room_id=room["id"]))

    def on_promote(self, session, msg):
        self.promote(msg["room_id"], msg["name"])
        session.send(p.make_msg(p.ACK, self.server.server_id, room_id=msg["room_id"]))

    def promote(self, room_id, name):
        """Take over a room as its new host, from the last replicated state."""
        s = self.server
        with self.lock:
            state = self.replicas.pop(room_id, None)
        room = Room.from_dict(state) if state else Room(room_id, name)
        room.all_disconnected()
        with s.rooms_lock:
            s.rooms[room_id] = room
        log.warning("PROMOTED to host of room %s (round %d, %d players must rejoin)",
                    room_id, room.round, len(room.players))

    # --- Leader side ----------------------------------------------------------------------
    def repair_rooms(self):
        """Leader: move rooms of crashed hosts to their backups, give every room a live backup.

        Returns True if the room table changed.
        """
        view = self.server.view
        changed = False
        with view.lock:
            for entry in list(view.rooms.values()):
                if entry["state"] == FINISHED:
                    continue
                room_id, host, backup = entry["room_id"], entry["host"], entry.get("backup")
                if host not in view.members:
                    if backup in view.members and self._promote(backup, entry):
                        log.warning("room %s: host %s crashed -> backup %s is the new host",
                                    room_id, host[:8], backup[:8])
                        host, backup = backup, None
                    else:
                        log.error("room %s LOST: host and backup crashed", room_id)
                        view.drop_room(room_id)
                        changed = True
                        continue
                if backup not in view.members or backup == host:
                    backup = view.successor(host)
                if (host, backup) != (entry["host"], entry.get("backup")):
                    view.set_room({**entry, "host": host, "backup": backup})
                    log.info("room %s: host %s, backup %s", room_id, host[:8],
                             backup[:8] if backup else "-")
                    changed = True
        return changed

    def _promote(self, backup, entry):
        s = self.server
        if backup == s.server_id:
            self.promote(entry["room_id"], entry["name"])
            return True
        try:
            reply = p.request(s.view.address(backup),
                              p.make_msg(p.PROMOTE, s.server_id, room_id=entry["room_id"],
                                         name=entry["name"]),
                              expect_reply=True)
            return reply is not None and reply["type"] == p.ACK
        except (OSError, ValueError) as e:
            log.warning("PROMOTE on %s failed: %s", backup[:8], e)
            return False

"""Group view and room table.

Only the leader changes them. Every change increments the version, and the leader sends the whole
view to all servers (VIEW_UPDATE). A server applies a received view only if (term, version) is
newer, so late or reordered updates cannot overwrite a newer view. The term counts elections:
a newly elected leader starts a new term, so its views win over everything of the old leader.

The ring used for the election (M3) and the backup choice (M4) is derived from the view:
the member UUIDs sorted, each server's successor is the next UUID (wrapping around).
"""

import threading

from common import config


class GroupView:
    def __init__(self):
        self.lock = threading.RLock()
        self.term = 0         # incremented by every newly elected leader
        self.version = 0
        self.leader = None    # UUID of the leader (None while an election runs)
        self.members = {}     # uuid -> {"uuid", "ip", "port"}
        self.rooms = {}       # room_id -> {"room_id", "name", "state", "players", "host"}

    # --- Sending and receiving ---------------------------------------------------------
    def to_dict(self):
        with self.lock:
            return {"term": self.term, "version": self.version, "leader": self.leader,
                    "members": [dict(m) for m in self.members.values()],
                    "rooms": [dict(r) for r in self.rooms.values()]}

    def apply(self, d):
        """Take over a view received from the leader. Returns False if it is not newer."""
        with self.lock:
            if (d["term"], d["version"]) <= (self.term, self.version):
                return False
            self.term = d["term"]
            self.version = d["version"]
            self.leader = d["leader"]
            self.members = {m["uuid"]: m for m in d["members"]}
            self.rooms = {r["room_id"]: r for r in d["rooms"]}
            return True

    # --- Changes (leader only; each one bumps the version) -------------------------------
    def add_member(self, uuid, ip, port):
        """Returns True if the view changed."""
        with self.lock:
            new = {"uuid": uuid, "ip": ip, "port": port}
            if self.members.get(uuid) == new:
                return False
            self.members[uuid] = new
            self.version += 1
            return True

    def remove_member(self, uuid):
        """Called when a server stops sending heartbeats."""
        with self.lock:
            if self.members.pop(uuid, None) is None:
                return False
            if self.leader == uuid:
                self.leader = None
            self.version += 1
            return True

    def become_leader(self, uuid):
        """Called on the winner of an election: start a new term."""
        with self.lock:
            self.leader = uuid
            self.term += 1
            self.version += 1

    def note_leader(self, uuid):
        """A follower learns the election result; the new leader's VIEW_UPDATE follows."""
        with self.lock:
            self.leader = uuid

    def set_room(self, entry):
        with self.lock:
            self.rooms[entry["room_id"]] = entry
            self.version += 1

    def drop_room(self, room_id):
        with self.lock:
            if self.rooms.pop(room_id, None) is not None:
                self.version += 1

    def update_room(self, summary):
        """Apply a ROOM_STATUS from a room's host (player count, state)."""
        with self.lock:
            entry = self.rooms.get(summary["room_id"])
            if entry is None:
                return False
            entry.update(state=summary["state"], players=summary["players"],
                         online=summary.get("online", 0))
            self.version += 1
            return True

    # --- Queries ---------------------------------------------------------------------
    def address(self, uuid):
        with self.lock:
            m = self.members.get(uuid)
            return (m["ip"], m["port"]) if m else None

    def others(self, own_uuid):
        with self.lock:
            return [dict(m) for u, m in self.members.items() if u != own_uuid]

    def ring(self):
        with self.lock:
            return sorted(self.members)

    def successor(self, uuid):
        """Next server in the ring (M4: backup of a room)."""
        following = self.successors(uuid)
        return following[0] if following else None

    def successors(self, uuid):
        """All other servers in ring order, starting after uuid (to skip unreachable ones)."""
        ring = self.ring()
        if uuid not in ring:
            ring = sorted(ring + [uuid])
        i = ring.index(uuid)
        return ring[i + 1:] + ring[:i]

    def hosts_by_load(self):
        """Members ordered by number of running rooms, fewest first (ties: by UUID)."""
        with self.lock:
            load = {u: 0 for u in self.members}
            for r in self.rooms.values():
                if r["state"] != "finished" and r["host"] in load:
                    load[r["host"]] += 1
            return sorted(load, key=lambda u: (load[u], u))

    def public_rooms(self):
        """Open rooms with their host's address, as sent to clients in GAME_LIST."""
        with self.lock:
            rooms = []
            for r in self.rooms.values():
                host = self.members.get(r["host"])
                if r["state"] == "finished" or host is None:
                    continue
                rooms.append({**r, "max_players": config.MAX_PLAYERS,
                              "host": {"ip": host["ip"], "port": host["port"]}})
            return rooms

    def public_room(self, room_id):
        return next((r for r in self.public_rooms() if r["room_id"] == room_id), None)

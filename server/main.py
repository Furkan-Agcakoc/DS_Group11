"""NumberRush game server.

Every server can host rooms. One server is the leader, elected with LCR (election.py): it keeps
the group view and room table (group_view.py), answers discovery requests (discovery.py) and
places new rooms on the server with the fewest running rooms. Heartbeats (heartbeat.py) detect
crashed servers and a crashed leader. Each room is run by exactly one host, which alone decides
about guesses and winners, and replicates every change to the room's backup (replication.py),
which takes over when the host crashes.

Threads: TCP listener, UDP broadcast listener, heartbeat sender/listener/monitor, room reporter,
one handler per TCP connection (clients and short-lived server-to-server requests share the
TCP port).

Run from the repository root:  python -m server.main [--port 6000] [--broadcast 192.168.0.255]
"""

import argparse
import logging
import queue
import socket
import threading
import time

from common import config, protocol as p
from server.discovery import Discovery
from server.election import Election
from server.game import FINISHED, GameError, Room
from server.group_view import GroupView
from server.heartbeat import Heartbeat
from server.replication import Replication

log = logging.getLogger("server")


class ClientSession:
    """One TCP connection. Several threads may send, so sending is locked."""

    def __init__(self, conn, addr):
        self.conn = conn
        self.addr = addr
        self.player_id = None
        self.room_id = None
        self._send_lock = threading.Lock()

    def send(self, msg):
        try:
            with self._send_lock:
                p.send_msg(self.conn, msg)
        except OSError:
            pass  # the handler thread notices the broken connection and cleans up


class GameServer:
    def __init__(self, port, broadcast_addr, ip=None):
        self.server_id = p.new_id()
        self.ip = ip or p.local_ip()
        self.port = port
        self.view = GroupView()
        self.joined_group = threading.Event()
        self.discovery = Discovery(self, broadcast_addr)
        self.election = Election(self)
        self.heartbeat = Heartbeat(self)
        self.replication = Replication(self)
        self.rooms = {}           # room_id -> Room hosted by this server
        self.rooms_lock = threading.Lock()
        self.sessions = {}        # player_id -> ClientSession
        self.sessions_lock = threading.Lock()
        self._room_reports = queue.Queue()  # ROOM_STATUS to the leader, sent in order

    @property
    def is_leader(self):
        return self.view.leader == self.server_id

    # --- Start-up ---------------------------------------------------------------------
    def run(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("0.0.0.0", self.port))
        listener.listen()
        # A timeout lets Ctrl+C stop the server on Windows, where accept() would block it.
        listener.settimeout(1.0)
        log.info("server %s on %s:%d", self.server_id[:8], self.ip, self.port)
        threading.Thread(target=self._accept_loop, args=(listener,), name="tcp-listener",
                         daemon=True).start()
        threading.Thread(target=self._report_loop, name="room-reporter", daemon=True).start()
        self.heartbeat.start()
        self.discovery.start()
        self.discovery.join_group()
        log.info("press Enter for the status of this server")
        while True:
            try:
                input()
            except EOFError:  # no console (e.g. started from a script)
                time.sleep(1)
                continue
            print(self.status())

    def status(self):
        """Human-readable state for the demo: role, ring, room table, hosted rooms, replicas."""
        v = self.view
        with v.lock:
            ring = " -> ".join(("*" if u == self.server_id else "") + u[:8]
                               + (" (leader)" if u == v.leader else "") for u in v.ring())
            lines = [
                f"=== {self.server_id[:8]} at {self.ip}:{self.port}: "
                f"{'LEADER' if self.is_leader else 'follower'} ===",
                f"view {v.term}.{v.version}, ring: {ring or '-'}",
                "room table:",
            ]
            for r in v.rooms.values():
                backup = r.get("backup")
                lines.append(f"  {r['room_id']} {r['name']:<12} {r['state']:<8} "
                             f"{r['players']} players  host {r['host'][:8]}  "
                             f"backup {backup[:8] if backup else '-'}")
        with self.rooms_lock:
            hosted = list(self.rooms.values())
        lines.append("hosted here: " + (", ".join(
            f"{r.id} (round {r.round}, {len(r.connected_players())}/{len(r.players)} online)"
            for r in hosted) or "-"))
        with self.replication.lock:
            lines.append("backup copies here: " + (", ".join(self.replication.replicas) or "-"))
        return "\n".join(lines)

    def _accept_loop(self, listener):
        while True:
            try:
                conn, addr = listener.accept()
            except socket.timeout:
                continue
            session = ClientSession(conn, addr)
            threading.Thread(target=self.handle_connection, args=(session,),
                             name=f"conn-{addr[0]}:{addr[1]}", daemon=True).start()

    def handle_connection(self, session):
        # Clients ping every PING_INTERVAL; silence for PING_TIMEOUT means the client is gone.
        session.conn.settimeout(config.PING_TIMEOUT)
        try:
            while True:
                msg = p.recv_msg(session.conn)
                if msg is None:
                    break
                try:
                    self.dispatch(session, msg)
                except GameError as e:
                    session.send(p.make_msg(p.ERROR, self.server_id, reason=str(e)))
        except socket.timeout:
            log.info("client %s:%d timed out", *session.addr)
        except (OSError, ValueError) as e:
            log.info("connection %s:%d: %s", *session.addr, e)
        finally:
            self.disconnect(session)
            session.conn.close()

    def dispatch(self, session, msg):
        handler = {
            # client -> server
            p.PING: self.on_ping,
            p.CREATE_ROOM: self.on_create_room,
            p.JOIN: self.on_join,
            p.REJOIN: self.on_rejoin,
            p.START_GAME: self.on_start,
            p.GUESS: self.on_guess,
            p.LEAVE: self.on_leave,
            # server -> server
            p.ELECTION: self.on_election,
            p.VIEW_UPDATE: self.on_view_update,
            p.HOST_ROOM: self.on_host_room,
            p.ROOM_STATUS: self.on_room_status,
            p.STATE_UPDATE: self.replication.on_state_update,
            p.PROMOTE: self.replication.on_promote,
        }.get(msg.get("type"))
        if handler is None:
            raise GameError(f"unknown message type {msg.get('type')!r}")
        handler(session, msg)

    # --- Group view (leader -> servers) ---------------------------------------------------
    def push_view(self):
        """Leader: send the current view to all other servers (in the background)."""
        snapshot = self.view.to_dict()
        targets = self.view.others(self.server_id)
        threading.Thread(target=self._send_view, args=(snapshot, targets), name="view-push",
                         daemon=True).start()
        self.replication.sync_backups()  # the leader may host rooms, too

    def _send_view(self, snapshot, targets):
        msg = p.make_msg(p.VIEW_UPDATE, self.server_id, view=snapshot)
        for m in targets:
            try:
                p.request((m["ip"], m["port"]), msg)
            except OSError as e:
                # Missing heartbeats, not a failed send, remove a server from the view.
                log.warning("VIEW_UPDATE to %s failed: %s", m["uuid"][:8], e)

    def on_view_update(self, session, msg):
        if self.view.apply(msg["view"]):
            log.info("view %d.%d: %d servers, %d rooms, leader %s", self.view.term,
                     self.view.version, len(self.view.members), len(self.view.rooms),
                     (self.view.leader or "-")[:8])
            if self.server_id in self.view.members:
                self.joined_group.set()
            self.replication.sync_backups()

    # --- Election and failures -------------------------------------------------------------
    def on_election(self, session, msg):
        # A server still waiting for a leader at start-up joins the running election.
        self.discovery.form_group()
        self.election.on_message(msg)

    def on_elected(self):
        """This server won the election: new term, send the view to everybody."""
        self.view.become_leader(self.server_id)
        self.heartbeat.reset()
        log.info("I am the LEADER now (term %d)", self.view.term)
        # The old leader may have crashed together with rooms it hosted.
        self.replication.repair_rooms()
        self.push_view()

    def on_leader_elected(self, leader):
        self.view.note_leader(leader)
        self.heartbeat.reset()

    def on_leader_failed(self, leader):
        self.view.remove_member(leader)
        self.election.start(f"leader {leader[:8]} failed")

    def on_server_failed(self, uuid):
        """Leader: remove a crashed server, move its rooms to their backups."""
        if self.view.remove_member(uuid):
            log.info("server %s removed from the view (%d servers left)", uuid[:8],
                     len(self.view.members))
            self.replication.repair_rooms()
            self.push_view()

    # --- Rooms: placement by the leader -----------------------------------------------------
    def on_create_room(self, session, msg):
        if not self.is_leader:
            raise GameError("this server is not the leader")
        room_id, name = p.new_id()[:6], (msg.get("name") or "room")[:32]
        for host in self.view.hosts_by_load():
            if self._place_room(host, room_id, name):
                break
        else:
            raise GameError("no server available")
        backup = self.view.successor(host)  # None while there is only one server
        self.view.set_room({"room_id": room_id, "name": name, "state": "waiting",
                            "players": 0, "host": host, "backup": backup})
        log.info("room %s (%s) placed on %s, backup %s", room_id, name, host[:8],
                 backup[:8] if backup else "-")
        self.push_view()
        session.send(p.make_msg(p.ROOM_CREATED, self.server_id,
                                room=self.view.public_room(room_id)))

    def _place_room(self, host, room_id, name):
        if host == self.server_id:
            self._host_room(room_id, name)
            return True
        try:
            reply = p.request(self.view.address(host),
                              p.make_msg(p.HOST_ROOM, self.server_id, room_id=room_id, name=name),
                              expect_reply=True)
            return reply is not None and reply["type"] == p.ACK
        except (OSError, ValueError) as e:
            log.warning("HOST_ROOM on %s failed: %s", host[:8], e)
            return False

    def on_host_room(self, session, msg):
        self._host_room(msg["room_id"], msg["name"])
        session.send(p.make_msg(p.ACK, self.server_id, room_id=msg["room_id"]))

    def _host_room(self, room_id, name):
        with self.rooms_lock:
            self.rooms.setdefault(room_id, Room(room_id, name))
        log.info("hosting room %s (%s)", room_id, name)

    # --- Rooms: status reports from host to leader -----------------------------------------
    def _report_room(self, room):
        self._room_reports.put(room.summary())

    def _report_loop(self):
        """One thread sends all ROOM_STATUS messages, so the leader gets them in order."""
        while True:
            summary = self._room_reports.get()
            if self.is_leader:
                self._apply_room_status(summary)
                continue
            leader = self.view.address(self.view.leader)
            if leader is None:
                continue
            try:
                p.request(leader, p.make_msg(p.ROOM_STATUS, self.server_id, room=summary))
            except OSError as e:
                log.warning("ROOM_STATUS to leader failed: %s", e)

    def on_room_status(self, session, msg):
        if self.is_leader:
            self._apply_room_status(msg["room"])

    def _apply_room_status(self, summary):
        if self.view.update_room(summary):
            self.push_view()

    # --- Game (on the room's host) ---------------------------------------------------------
    def on_ping(self, session, msg):
        session.send(p.make_msg(p.PONG, self.server_id))

    def on_join(self, session, msg):
        if session.player_id:
            raise GameError("already in a room")
        room = self._room(msg.get("room_id"))
        with room.lock:
            player = room.add_player(str(msg.get("name", "")))
            session.player_id, session.room_id = player.id, room.id
            with self.sessions_lock:
                self.sessions[player.id] = session
            self._replicate(room)
        log.info("%s joined room %s", player.name, room.id)
        session.send(p.make_msg(p.JOINED, self.server_id, room=room.summary(),
                                player_id=player.id, name=player.name))
        self._broadcast_room_update(room)

    def on_rejoin(self, session, msg):
        """A player comes back after a crash of its client or of the room's old host."""
        room = self._room(msg.get("room_id"))
        with room.lock:
            player = room.rejoin(msg.get("player_id"))
            session.player_id, session.room_id = player.id, room.id
            with self.sessions_lock:
                self.sessions[player.id] = session
            self._replicate(room)
            log.info("%s REJOINED room %s (score %d)", player.name, room.id, player.score)
            session.send(p.make_msg(p.JOINED, self.server_id, room=room.summary(),
                                    player_id=player.id, name=player.name, rejoined=True,
                                    round=room.round, rounds=config.ROUNDS,
                                    attempts=player.attempts, score=player.score))
            self._broadcast_room_update(room)

    def on_start(self, session, msg):
        room = self._session_room(session)
        with room.lock:
            room.start()
            self._replicate(room)
            self._report_room(room)
            log.info("room %s: game started", room.id)
            self._broadcast_round_start(room)

    def on_guess(self, session, msg):
        room = self._session_room(session)
        value = msg.get("value")
        with room.lock:
            feedback, attempts, duplicate = room.guess(session.player_id, msg.get("guess_id"),
                                                       value)
            if not duplicate:
                # The backup must ACK this state before the player gets feedback.
                self._replicate(room)
            session.send(p.make_msg(p.FEEDBACK, self.server_id, guess_id=msg.get("guess_id"),
                                    value=value, feedback=feedback, attempts=attempts,
                                    max_attempts=config.MAX_ATTEMPTS))
            self._advance_if_round_complete(room)

    def on_leave(self, session, msg):
        self.disconnect(session)
        session.player_id = session.room_id = None

    def disconnect(self, session):
        if not session.player_id:
            return
        with self.sessions_lock:
            if self.sessions.get(session.player_id) is not session:
                return  # the player has already rejoined on a newer connection
            del self.sessions[session.player_id]
        with self.rooms_lock:
            room = self.rooms.get(session.room_id)
        if room is None:
            return
        with room.lock:
            # During a game the seat and score are kept for REJOIN_GRACE seconds.
            room.remove_player(session.player_id)
            self._replicate(room)
            log.info("player %s left room %s", session.player_id[:8], room.id)
            self._broadcast_room_update(room)
            # The leaving player may have been the last one the round was waiting for.
            self._advance_if_round_complete(room)

    def _advance_if_round_complete(self, room):
        if not room.round_complete():
            return
        result = room.finish_round()
        self._replicate(room)
        self._broadcast(room, p.make_msg(p.ROUND_RESULT, self.server_id, **result))
        if room.state == FINISHED:
            log.info("room %s: game over", room.id)
            self._report_room(room)
            self._broadcast(room, p.make_msg(p.GAME_OVER, self.server_id,
                                             scores=room.scoreboard()))
        else:
            self._broadcast_round_start(room)

    def _broadcast_round_start(self, room):
        self._broadcast(room, p.make_msg(
            p.ROUND_START, self.server_id, round=room.round, rounds=config.ROUNDS,
            min=config.NUMBER_MIN, max=config.NUMBER_MAX, max_attempts=config.MAX_ATTEMPTS))

    def _broadcast_room_update(self, room):
        self._report_room(room)
        self._broadcast(room, p.make_msg(p.ROOM_UPDATE, self.server_id,
                                         room=room.summary(), players=room.scoreboard()))

    def _broadcast(self, room, msg):
        """Send to every connected player of a room (TCP unicast to each)."""
        with self.sessions_lock:
            targets = [self.sessions[pid] for pid in room.players if pid in self.sessions]
        for s in targets:
            s.send(msg)

    def _replicate(self, room):
        self.replication.replicate(room)

    def _room(self, room_id):
        with self.rooms_lock:
            room = self.rooms.get(room_id)
        if room is None:
            raise GameError(f"room {room_id!r} is not hosted here")
        return room

    def _session_room(self, session):
        if not session.room_id:
            raise GameError("join a room first")
        return self._room(session.room_id)


def main():
    parser = argparse.ArgumentParser(description="NumberRush server")
    parser.add_argument("--port", type=int, default=config.TCP_PORT,
                        help="TCP port (use different ports for several servers on one machine)")
    parser.add_argument("--broadcast", default=config.BROADCAST_ADDR,
                        help="broadcast address, e.g. 192.168.0.255")
    parser.add_argument("--ip", help="own LAN IP, if the detected one is wrong (VPN, VirtualBox …)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S",
                        format="%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s")
    try:
        GameServer(args.port, args.broadcast, args.ip).run()
    except KeyboardInterrupt:
        log.info("server stopped")


if __name__ == "__main__":
    main()

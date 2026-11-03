"""NumberRush command-line client.

The client needs no server address: it broadcasts DISCOVER_GAME and the leader replies with the
open rooms and each room's host. New rooms are requested from the leader; the game itself runs
over a TCP connection to the room's host.

If the connection to the host breaks (host crash: no PONG / connection reset), the client
rediscovers the room's new host and sends REJOIN with its player ID. A guess that was not
answered yet is resent with the same guess ID, so the host never counts it twice.

Threads: the main thread reads commands; while in a room, a receiver thread prints the host's
messages and a pinger thread sends PING every PING_INTERVAL seconds.

Run from the repository root:  python -m client.main --name Alice [--broadcast 192.168.0.255]
"""

import argparse
import socket
import threading
import time

from common import config, protocol as p

HELP = """Commands:
  list            search for games (UDP broadcast)
  create <name>   create a room and join it
  join <room_id>  join a room
  start           start the game (at least 2 players)
  <number>        guess a number
  leave           leave the room
  quit            exit"""


class Client:
    def __init__(self, name, broadcast_addr):
        self.name = name
        self.broadcast_addr = broadcast_addr
        self.client_id = p.new_id()  # sender ID until a host assigns a player ID
        self.player_id = None
        self.room_id = None
        self.pending_guess = None    # (guess_id, value) sent but not answered yet
        self.leader = None           # (ip, port) from the last GAME_LIST
        self.rooms = {}              # room_id -> room info from the last GAME_LIST
        self.conn = None             # TCP connection to the current room's host
        self._send_lock = threading.Lock()

    # --- Discovery -----------------------------------------------------------------------
    def discover(self, show=True):
        """Broadcast DISCOVER_GAME until the leader answers. Returns True on success."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(config.PING_INTERVAL)
        request = p.encode(p.make_msg(p.DISCOVER_GAME, self.client_id))
        try:
            for _ in range(config.DISCOVER_RETRIES):
                sock.sendto(request, (self.broadcast_addr, config.BROADCAST_PORT))
                try:
                    data, _ = sock.recvfrom(65535)
                    msg = p.decode(data)
                except (socket.timeout, ValueError):
                    continue
                if msg.get("type") == p.GAME_LIST:
                    self.leader = (msg["leader"]["ip"], msg["leader"]["port"])
                    self.rooms = {r["room_id"]: r for r in msg["rooms"]}
                    if show:
                        self._print_rooms()
                    return True
        finally:
            sock.close()
        if show:
            print("No game server found. Are you in the same network (not eduroam)?")
        return False

    def _print_rooms(self):
        if not self.rooms:
            print("No open rooms. Create one with: create <name>")
        for r in self.rooms.values():
            print(f"  {r['room_id']}  {r['name']:<16} {r['players']}/{r['max_players']}  "
                  f"{r['state']:<8} host {r['host']['ip']}:{r['host']['port']}")

    # --- Lobby commands ------------------------------------------------------------------
    def create(self, room_name):
        if not self.discover(show=False):
            return
        try:
            reply = p.request(self.leader, p.make_msg(p.CREATE_ROOM, self.client_id, name=room_name),
                              expect_reply=True, timeout=config.PING_TIMEOUT)
        except (OSError, ValueError) as e:
            print(f"Leader not reachable: {e}")
            return
        if reply is None or reply["type"] != p.ROOM_CREATED:
            print(f"Error: {reply['reason'] if reply else 'no answer'}")
            return
        room = reply["room"]
        self.rooms[room["room_id"]] = room
        print(f"Room '{room['name']}' created with ID {room['room_id']}.")
        self.join(room["room_id"])

    def join(self, room_id):
        if self.conn:
            print("You are already in a room. Type 'leave' first.")
            return
        if room_id not in self.rooms and not (self.discover(show=False) and room_id in self.rooms):
            print(f"Unknown room {room_id!r}. Type 'list' to see open rooms.")
            return
        try:
            self._connect(room_id)
        except OSError as e:
            print(f"Host not reachable: {e}")
            return
        self.send(p.JOIN, room_id=room_id, name=self.name)

    def _connect(self, room_id):
        host = self.rooms[room_id]["host"]
        conn = socket.create_connection((host["ip"], host["port"]), timeout=config.PING_TIMEOUT)
        self.conn = conn
        threading.Thread(target=self.receive_loop, args=(conn,), name="receiver", daemon=True).start()
        threading.Thread(target=self.ping_loop, args=(conn,), name="pinger", daemon=True).start()

    def rejoin(self):
        """After losing the host: find the room's (new) host and REJOIN within the grace period."""
        print("\n!! Connection to the room host lost. Searching for the room's new host ...")
        deadline = time.monotonic() + config.REJOIN_GRACE
        while time.monotonic() < deadline:
            # The leader needs a few seconds to notice the crash and promote the backup;
            # until then the room list may still show the old host.
            if self.discover(show=False) and self.room_id in self.rooms:
                try:
                    self._connect(self.room_id)
                    self.send_on(self.conn, p.REJOIN, room_id=self.room_id,
                                 player_id=self.player_id)
                    if self.pending_guess:
                        guess_id, value = self.pending_guess
                        self.send_on(self.conn, p.GUESS, guess_id=guess_id, value=value)
                    return
                except OSError:
                    self.conn = None
            time.sleep(config.PING_INTERVAL)
        print("!! Could not get back into the room. Type 'list' to search for games.")
        self.player_id = self.room_id = self.pending_guess = None

    def leave(self):
        if self.conn:
            conn, self.conn, self.player_id, self.room_id = self.conn, None, None, None
            try:
                self.send_on(conn, p.LEAVE)
            except OSError:
                pass
            conn.close()
            print("Left the room.")

    # --- Connection to the room host --------------------------------------------------------
    def send(self, msg_type, **fields):
        if self.conn is None:
            print("Join a room first.")
            return
        try:
            self.send_on(self.conn, msg_type, **fields)
        except OSError:
            print("Connection to the host lost.")

    def send_on(self, conn, msg_type, **fields):
        msg = p.make_msg(msg_type, self.player_id or self.client_id, **fields)
        with self._send_lock:
            p.send_msg(conn, msg)

    def receive_loop(self, conn):
        try:
            while True:
                msg = p.recv_msg(conn)
                if msg is None:
                    break
                self.handle(msg)
        except (OSError, ValueError):
            pass
        conn.close()
        if self.conn is conn:  # not left on purpose
            self.conn = None
            if self.player_id and self.room_id:
                self.rejoin()
            else:
                print("\n!! Connection to the room host lost. Type 'list' to search again.")

    def ping_loop(self, conn):
        while self.conn is conn:
            time.sleep(config.PING_INTERVAL)
            try:
                self.send_on(conn, p.PING)
            except OSError:
                return

    # --- Messages from the host -----------------------------------------------------------
    def handle(self, msg):
        t = msg["type"]
        if t == p.PONG:
            return
        if t == p.JOINED:
            self.player_id, self.room_id = msg["player_id"], msg["room"]["room_id"]
            if msg.get("rejoined"):
                print(f"Back in room {self.room_id} (round {msg['round']}/{msg['rounds']}, "
                      f"your attempts this round: {msg['attempts']}, score: {msg['score']}).")
            else:
                print(f"Joined room {self.room_id} as {msg['name']}. "
                      f"Type 'start' when everyone is in.")
        elif t == p.ROOM_UPDATE:
            names = ", ".join(pl["name"] + ("" if pl["connected"] else " (offline)")
                              for pl in msg["players"])
            print(f"Players in room: {names}")
        elif t == p.ROUND_START:
            print(f"\n=== Round {msg['round']}/{msg['rounds']}: guess a number between "
                  f"{msg['min']} and {msg['max']} (max. {msg['max_attempts']} attempts) ===")
        elif t == p.FEEDBACK:
            if self.pending_guess and self.pending_guess[0] == msg["guess_id"]:
                self.pending_guess = None
            text = {"higher": "Higher!", "lower": "Lower!", "correct": "Correct!",
                    "out_of_attempts": "No attempts left for this round."}[msg["feedback"]]
            print(f"{msg['value']}: {text}  (attempt {msg['attempts']}/{msg['max_attempts']})")
        elif t == p.ROUND_RESULT:
            print(f"\n--- Round {msg['round']} over, the number was {msg['secret']} ---")
            for place, r in enumerate(msg["ranking"], 1):
                print(f"  {place}. {r['name']}: {r['attempts']} attempts, +{r['points']}")
            if not msg["ranking"]:
                print("  Nobody found the number.")
            print("Scores: " + ", ".join(f"{s['name']} {s['score']}" for s in msg["scores"]))
        elif t == p.GAME_OVER:
            best = msg["scores"][0]
            print(f"\n*** Game over! Winner: {best['name']} with {best['score']} points ***")
        elif t == p.ERROR:
            print(f"Error: {msg['reason']}")
        else:
            print(f"(unhandled message {t})")

    # --- User input -------------------------------------------------------------------
    def command(self, line):
        # PowerShell pipes may prepend a byte-order mark to the first line.
        cmd, _, arg = line.strip().lstrip("﻿").partition(" ")
        cmd = cmd.lower()
        if not cmd:
            return
        if cmd.isdigit():
            self.pending_guess = (p.new_id(), int(cmd))
            self.send(p.GUESS, guess_id=self.pending_guess[0], value=self.pending_guess[1])
        elif cmd == "list":
            self.discover()
        elif cmd == "create":
            self.create(arg.strip() or f"{self.name}'s room")
        elif cmd == "join":
            self.join(arg.strip())
        elif cmd == "start":
            self.send(p.START_GAME)
        elif cmd == "leave":
            self.leave()
        elif cmd in ("quit", "exit"):
            raise SystemExit
        else:
            print(HELP)

    def run(self):
        print(HELP)
        self.discover()
        while True:
            try:
                self.command(input())
            except (EOFError, KeyboardInterrupt, SystemExit):
                break
        self.leave()


def main():
    parser = argparse.ArgumentParser(description="NumberRush client")
    parser.add_argument("--name")
    parser.add_argument("--broadcast", default=config.BROADCAST_ADDR,
                        help="broadcast address, e.g. 192.168.0.255")
    args = parser.parse_args()
    name = args.name or input("Your name: ")
    Client(name, args.broadcast).run()


if __name__ == "__main__":
    main()

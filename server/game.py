"""Game logic of one room. No sockets here, so it can be tested and replicated on its own.

The room host is the only writer of a room's state. Ties are broken by the order in which
correct guesses arrive at the host (a per-room sequence number), so no client clocks are needed.
Every answered guess is stored with its guess ID, so a guess that a client resends after a
host crash is answered again but never counted twice.
"""

import random
import threading
import time

from common import config
from common.protocol import new_id

WAITING, PLAYING, FINISHED = "waiting", "playing", "finished"


class GameError(Exception):
    """Invalid request from a player; the message is sent back as ERROR."""


class Player:
    def __init__(self, player_id, name):
        self.id = player_id
        self.name = name
        self.connected = True
        self.score = 0
        self.answers = {}  # guess_id -> [feedback, attempts] of every counted guess
        self.reset_round()

    def reset_round(self):
        self.attempts = 0
        self.solved = False
        self.solved_seq = None  # arrival position of the correct guess at the host

    @property
    def done(self):
        return self.solved or self.attempts >= config.MAX_ATTEMPTS

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "connected": self.connected, "score": self.score,
            "attempts": self.attempts, "solved": self.solved, "solved_seq": self.solved_seq,
            "answers": self.answers,
        }

    @classmethod
    def from_dict(cls, d):
        p = cls(d["id"], d["name"])
        p.connected = d["connected"]
        p.score = d["score"]
        p.attempts = d["attempts"]
        p.solved = d["solved"]
        p.solved_seq = d["solved_seq"]
        p.answers = dict(d["answers"])
        return p


class Room:
    def __init__(self, room_id, name, rng=None):
        self.id = room_id
        self.name = name
        self.state = WAITING
        self.round = 0
        self.secret = None
        self.seq = 0            # counts accepted guesses in arrival order
        self.players = {}       # player_id -> Player, in join order
        self.lock = threading.RLock()  # held by the server while it changes this room
        self.disconnected_since = {}   # player_id -> local time of disconnect (not replicated)
        self._rng = rng or random.Random()

    # --- Membership ---------------------------------------------------------------
    def add_player(self, name):
        if self.state != WAITING:
            raise GameError("game already started")
        if len(self.players) >= config.MAX_PLAYERS:
            raise GameError("room is full")
        name = name.strip() or "player"
        if any(p.name == name for p in self.players.values()):
            raise GameError(f"name '{name}' is taken in this room")
        player = Player(new_id(), name)
        self.players[player.id] = player
        return player

    def set_connected(self, player_id, connected):
        player = self.players.get(player_id)
        if player is None:
            return
        player.connected = connected
        if connected:
            self.disconnected_since.pop(player_id, None)
        else:
            self.disconnected_since.setdefault(player_id, time.monotonic())

    def remove_player(self, player_id):
        """Only used while waiting; during a game a leaving player keeps the seat for a rejoin."""
        if self.state == WAITING:
            self.players.pop(player_id, None)
        else:
            self.set_connected(player_id, False)

    def rejoin(self, player_id):
        """A player comes back (after a client or host crash) with seat, score and attempts."""
        player = self.players.get(player_id)
        if player is None:
            raise GameError("unknown player")
        since = self.disconnected_since.get(player_id)
        if since is not None and time.monotonic() - since > config.REJOIN_GRACE:
            raise GameError(f"seat expired after {config.REJOIN_GRACE:.0f} s")
        self.set_connected(player_id, True)
        return player

    def all_disconnected(self):
        """Called on the backup when it takes over: everybody has to rejoin within the grace period."""
        for pid in self.players:
            self.set_connected(pid, False)

    def connected_players(self):
        return [p for p in self.players.values() if p.connected]

    # --- Game flow ----------------------------------------------------------------
    def start(self):
        if self.state != WAITING:
            raise GameError("game already started")
        if len(self.connected_players()) < config.MIN_PLAYERS:
            raise GameError(f"at least {config.MIN_PLAYERS} players needed")
        self.state = PLAYING
        self._new_round()

    def _new_round(self):
        self.round += 1
        self.secret = self._rng.randint(config.NUMBER_MIN, config.NUMBER_MAX)
        for p in self.players.values():
            p.reset_round()

    def guess(self, player_id, guess_id, value):
        """Evaluate a guess. Returns (feedback, attempts, duplicate).

        A resent guess (same guess ID) gets the stored answer again with duplicate=True.
        """
        player = self.players.get(player_id)
        if player is None:
            raise GameError("unknown player")
        if guess_id in player.answers:
            feedback, attempts = player.answers[guess_id]
            return feedback, attempts, True
        if self.state != PLAYING:
            raise GameError("no round is running")
        if player.done:
            raise GameError("you are done for this round")
        if not isinstance(value, int) or not config.NUMBER_MIN <= value <= config.NUMBER_MAX:
            raise GameError(f"guess must be between {config.NUMBER_MIN} and {config.NUMBER_MAX}")

        player.attempts += 1
        self.seq += 1
        if value == self.secret:
            player.solved = True
            player.solved_seq = self.seq
            feedback = "correct"
        elif player.attempts >= config.MAX_ATTEMPTS:
            feedback = "out_of_attempts"
        else:
            feedback = "higher" if self.secret > value else "lower"
        player.answers[guess_id] = [feedback, player.attempts]
        return feedback, player.attempts, False

    def round_complete(self):
        """A round ends when every connected player has solved it or used all attempts.

        With nobody connected (e.g. right after a host crash) the round waits for rejoins.
        """
        connected = self.connected_players()
        return self.state == PLAYING and bool(connected) and all(p.done for p in connected)

    def finish_round(self):
        """Award points, then start the next round or end the game. Returns the round result."""
        solved = sorted((p for p in self.players.values() if p.solved),
                        key=lambda p: (p.attempts, p.solved_seq))
        ranking = []
        for place, p in enumerate(solved):
            points = config.POINTS[place] if place < len(config.POINTS) else 0
            p.score += points
            ranking.append({"name": p.name, "attempts": p.attempts, "points": points})

        result = {"round": self.round, "secret": self.secret, "ranking": ranking,
                  "scores": self.scoreboard()}
        if self.round >= config.ROUNDS:
            self.state = FINISHED
            self.secret = None
        else:
            self._new_round()
        return result

    def scoreboard(self):
        return [{"name": p.name, "score": p.score, "connected": p.connected}
                for p in sorted(self.players.values(), key=lambda p: -p.score)]

    def summary(self):
        """Public room info for room lists (never contains the secret)."""
        return {"room_id": self.id, "name": self.name, "state": self.state,
                "players": len(self.players), "max_players": config.MAX_PLAYERS}

    # --- Replication ---------------------------------------------------------------------
    def to_dict(self):
        return {"id": self.id, "name": self.name, "state": self.state, "round": self.round,
                "secret": self.secret, "seq": self.seq,
                "players": [p.to_dict() for p in self.players.values()]}

    @classmethod
    def from_dict(cls, d):
        room = cls(d["id"], d["name"])
        room.state = d["state"]
        room.round = d["round"]
        room.secret = d["secret"]
        room.seq = d["seq"]
        for pd in d["players"]:
            p = Player.from_dict(pd)
            room.players[p.id] = p
        return room

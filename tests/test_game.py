"""Unit tests for the room game logic.  Run from the repository root:  python -m unittest"""

import random
import unittest

from common import config
from common.protocol import new_id
from server.game import FINISHED, PLAYING, GameError, Room


def play_until_correct(room, player, low=config.NUMBER_MIN, high=config.NUMBER_MAX):
    """Binary search, like a sensible player."""
    while True:
        mid = (low + high) // 2
        feedback, _, _ = room.guess(player.id, new_id(), mid)
        if feedback == "correct":
            return
        if feedback == "higher":
            low = mid + 1
        else:
            high = mid - 1


class RoomTest(unittest.TestCase):
    def setUp(self):
        self.room = Room("r1", "test", rng=random.Random(42))
        self.alice = self.room.add_player("Alice")
        self.bob = self.room.add_player("Bob")

    def test_start_needs_two_players(self):
        room = Room("r2", "solo")
        room.add_player("Alice")
        with self.assertRaises(GameError):
            room.start()

    def test_feedback_direction(self):
        self.room.start()
        secret = self.room.secret
        if secret > config.NUMBER_MIN:
            self.assertEqual(self.room.guess(self.alice.id, new_id(), secret - 1)[0], "higher")
        if secret < config.NUMBER_MAX:
            self.assertEqual(self.room.guess(self.alice.id, new_id(), secret + 1)[0], "lower")
        self.assertEqual(self.room.guess(self.alice.id, new_id(), secret)[0], "correct")

    def test_resent_guess_is_not_counted_twice(self):
        self.room.start()
        gid = new_id()
        wrong = config.NUMBER_MIN if self.room.secret != config.NUMBER_MIN else config.NUMBER_MAX
        first = self.room.guess(self.alice.id, gid, wrong)
        feedback, attempts, duplicate = self.room.guess(self.alice.id, gid, wrong)
        self.assertTrue(duplicate)
        self.assertEqual((feedback, attempts), first[:2])  # same answer again
        self.assertEqual(self.alice.attempts, 1)

    def test_backup_takes_over_and_players_rejoin(self):
        self.room.start()
        gid = new_id()
        self.room.guess(self.alice.id, gid, self.room.secret)
        backup = Room.from_dict(self.room.to_dict())     # state as replicated before the crash
        backup.all_disconnected()
        self.assertFalse(backup.round_complete())        # waits for rejoins
        backup.rejoin(self.alice.id)
        self.assertEqual(backup.guess(self.alice.id, gid, backup.secret)[2], True)
        self.assertEqual(backup.players[self.alice.id].attempts, 1)
        with self.assertRaises(GameError):
            backup.rejoin("someone-else")

    def test_rejoin_by_name_after_going_offline(self):
        self.room.start()
        self.room.guess(self.bob.id, new_id(), self.room.secret)
        self.room.remove_player(self.bob.id)              # leave / crash during the game
        player, rejoined = self.room.join("Bob")
        self.assertTrue(rejoined)
        self.assertIs(player, self.bob)
        self.assertEqual((player.attempts, player.solved), (1, True))

    def test_join_by_name_refused_while_online_or_unknown(self):
        self.room.start()
        with self.assertRaises(GameError):
            self.room.join("Bob")                         # Bob is still connected
        with self.assertRaises(GameError):
            self.room.join("Carol")                       # new players cannot join a running game

    def test_afk_player_is_kicked_and_cannot_rejoin(self):
        self.room.start()
        self.room.last_active[self.bob.id] -= config.AFK_TIMEOUT + 1
        self.assertEqual(self.room.afk_players(), [self.bob])
        self.room.kick(self.bob.id)
        self.assertEqual(self.bob.status, "kicked")
        with self.assertRaises(GameError):
            self.room.join("Bob")
        copy = Room.from_dict(self.room.to_dict())        # the kick is replicated
        self.assertTrue(copy.players[self.bob.id].kicked)

    def test_waiting_and_done_players_are_not_afk(self):
        self.room.last_active[self.bob.id] = -1e9          # long ago, but the game has not started
        self.assertEqual(self.room.afk_players(), [])
        self.room.start()
        self.room.guess(self.alice.id, new_id(), self.room.secret)  # Alice is done
        self.room.last_active[self.alice.id] -= config.AFK_TIMEOUT + 1
        self.assertEqual(self.room.afk_players(), [])

    def test_kicking_the_last_guesser_completes_the_round(self):
        self.room.start()
        self.room.guess(self.alice.id, new_id(), self.room.secret)
        self.room.kick(self.bob.id)
        self.assertTrue(self.room.round_complete())

    def test_game_ends_when_nobody_is_left(self):
        self.room.start()
        self.room.kick(self.alice.id)
        self.room.remove_player(self.bob.id)
        self.assertFalse(self.room.nobody_left())          # Bob may still come back
        self.room.disconnected_since[self.bob.id] -= config.REJOIN_GRACE + 1
        self.assertTrue(self.room.nobody_left())

    def test_summary_counts_online_players(self):
        self.room.start()
        self.room.remove_player(self.bob.id)
        s = self.room.summary()
        self.assertEqual((s["online"], s["players"]), (1, 2))

    def test_seat_expires_after_grace_period(self):
        self.room.start()
        self.room.set_connected(self.bob.id, False)
        self.room.disconnected_since[self.bob.id] -= config.REJOIN_GRACE + 1
        with self.assertRaises(GameError):
            self.room.rejoin(self.bob.id)

    def test_tie_broken_by_arrival_order(self):
        self.room.start()
        # Both need one attempt; Bob's correct guess arrives first.
        self.room.guess(self.bob.id, new_id(), self.room.secret)
        self.room.guess(self.alice.id, new_id(), self.room.secret)
        self.assertTrue(self.room.round_complete())
        result = self.room.finish_round()
        self.assertEqual([r["name"] for r in result["ranking"]], ["Bob", "Alice"])
        self.assertEqual((self.bob.score, self.alice.score), (3, 2))

    def test_fewest_attempts_win(self):
        self.room.start()
        wrong = config.NUMBER_MIN if self.room.secret != config.NUMBER_MIN else config.NUMBER_MAX
        self.room.guess(self.alice.id, new_id(), wrong)
        self.room.guess(self.alice.id, new_id(), self.room.secret)  # 2 attempts, arrives first
        self.room.guess(self.bob.id, new_id(), self.room.secret)    # 1 attempt
        result = self.room.finish_round()
        self.assertEqual(result["ranking"][0]["name"], "Bob")

    def test_out_of_attempts_ends_round_for_player(self):
        self.room.start()
        wrong = config.NUMBER_MIN if self.room.secret != config.NUMBER_MIN else config.NUMBER_MAX
        for _ in range(config.MAX_ATTEMPTS - 1):
            self.room.guess(self.alice.id, new_id(), wrong)
        self.assertEqual(self.room.guess(self.alice.id, new_id(), wrong)[0], "out_of_attempts")
        with self.assertRaises(GameError):
            self.room.guess(self.alice.id, new_id(), wrong)

    def test_disconnected_player_does_not_block_round(self):
        self.room.start()
        self.room.set_connected(self.bob.id, False)
        self.room.guess(self.alice.id, new_id(), self.room.secret)
        self.assertTrue(self.room.round_complete())

    def test_full_game(self):
        self.room.start()
        for _ in range(config.ROUNDS):
            self.assertEqual(self.room.state, PLAYING)
            play_until_correct(self.room, self.alice)
            play_until_correct(self.room, self.bob)
            self.room.finish_round()
        self.assertEqual(self.room.state, FINISHED)
        self.assertEqual(self.alice.score + self.bob.score, config.ROUNDS * 5)

    def test_state_roundtrip_for_replication(self):
        self.room.start()
        self.room.guess(self.alice.id, new_id(), self.room.secret)
        copy = Room.from_dict(self.room.to_dict())
        self.assertEqual(copy.to_dict(), self.room.to_dict())


if __name__ == "__main__":
    unittest.main()

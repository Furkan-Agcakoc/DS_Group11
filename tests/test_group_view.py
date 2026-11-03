"""Unit tests for the group view and room table.  Run from the repository root:  python -m unittest"""

import unittest

from server.group_view import GroupView


class GroupViewTest(unittest.TestCase):
    def setUp(self):
        self.view = GroupView()
        for uuid, port in (("b", 6002), ("a", 6001), ("c", 6003)):
            self.view.add_member(uuid, "10.0.0.1", port)
        self.view.become_leader("c")

    def test_version_grows_with_every_change(self):
        v = self.view.version
        self.assertFalse(self.view.add_member("a", "10.0.0.1", 6001))  # unchanged
        self.assertEqual(self.view.version, v)
        self.view.add_member("d", "10.0.0.2", 6000)
        self.assertEqual(self.view.version, v + 1)

    def test_older_view_is_ignored(self):
        other = GroupView()
        self.assertTrue(other.apply(self.view.to_dict()))
        old = self.view.to_dict()
        self.view.add_member("d", "10.0.0.2", 6000)
        self.assertTrue(other.apply(self.view.to_dict()))
        self.assertFalse(other.apply(old))
        self.assertIn("d", other.members)

    def test_new_term_wins_over_higher_version(self):
        follower = GroupView()
        follower.apply(self.view.to_dict())
        for i in range(5):                       # old leader's view with a high version
            follower.add_member(f"x{i}", "10.0.0.9", 7000 + i)
        new_leader = GroupView()
        new_leader.apply(self.view.to_dict())
        new_leader.become_leader("b")
        self.assertTrue(follower.apply(new_leader.to_dict()))
        self.assertEqual(follower.leader, "b")

    def test_removing_the_leader_clears_it(self):
        self.view.remove_member("c")
        self.assertIsNone(self.view.leader)

    def test_ring_sorted_by_uuid_with_wraparound(self):
        self.assertEqual(self.view.ring(), ["a", "b", "c"])
        self.assertEqual(self.view.successor("a"), "b")
        self.assertEqual(self.view.successor("c"), "a")

    def test_least_loaded_host_first(self):
        self.view.set_room({"room_id": "r1", "name": "x", "state": "waiting", "players": 0, "host": "a"})
        self.view.set_room({"room_id": "r2", "name": "y", "state": "finished", "players": 0, "host": "b"})
        self.assertEqual(self.view.hosts_by_load(), ["b", "c", "a"])

    def test_public_rooms_hide_finished_and_add_host_address(self):
        self.view.set_room({"room_id": "r1", "name": "x", "state": "waiting", "players": 1, "host": "a"})
        self.view.set_room({"room_id": "r2", "name": "y", "state": "finished", "players": 0, "host": "b"})
        rooms = self.view.public_rooms()
        self.assertEqual([r["room_id"] for r in rooms], ["r1"])
        self.assertEqual(rooms[0]["host"], {"ip": "10.0.0.1", "port": 6001})


if __name__ == "__main__":
    unittest.main()

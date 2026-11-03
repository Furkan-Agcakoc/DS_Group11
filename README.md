# NumberRush – Group 11

Distributed multiplayer number-guessing game (Distributed Systems, WS 26/27, HHZ).
Players join rooms of 2–6 people and play 5 rounds; in each round the room's host draws a number
from 1 to 100, players guess privately and get "higher", "lower" or "correct". Fewest attempts win
the round (3/2/1 points); ties are broken by the arrival order of correct guesses at the host.

Python 3.10+, standard library only.

## Run

All commands from the repository root. Start one or more servers (on one machine each needs its
own `--port`), then clients. Nobody needs to know an IP address: servers and clients find each
other via UDP broadcast.

```
python -m server.main --port 6000
python -m server.main --port 6001        # joins the group of the first server
python -m client.main --name Alice
python -m client.main --name Bob
```

In the client: `list` (search for games), `create <name>` (the leader places the room on the
server with the fewest rooms; you join it), `join <room_id>`, `start`, then type numbers to guess.
`quit` exits.

Press Enter in a server window to print its status (role, ring, room table, hosted rooms,
backup copies). The demo script is in [DEMO.md](DEMO.md).

If discovery finds nothing (several network adapters, VPN, VirtualBox …), pass the subnet's
broadcast address to servers and clients, e.g. `--broadcast 192.168.0.255`. If a server shows a
wrong IP at start-up, set it with `--ip`. On first start,
allow Python through the Windows firewall (private networks). Broadcast is blocked on eduroam;
use your own router or a phone hotspot.

To see the election: start 2–3 servers within 3 seconds of each other (they elect the highest
UUID), then stop the leader with Ctrl+C or by closing its window. After 3 s the others notice the
missing heartbeats and elect a new leader; clients find it with `list`.

To see the failover: create a room, play, and stop the room's host server in the middle of a
round (its log says `hosting room …`). After 3 s the leader promotes the backup (`PROMOTED to host`),
the clients reconnect on their own (`Back in room …`) and keep their score and attempts.

Known limits:
- A server that starts while an election is running may form its own group.
  Start servers one after another or all within 3 s.
- If a room's host and backup crash at the same time, the room is lost.
- Only crash and omission faults are handled, no Byzantine faults.

Tests: `python -m unittest`

## Structure

```
common/config.py      all ports, timeouts and game rules
common/protocol.py    message types; JSON with type/sender/msg_id; TCP length prefix; request()
server/discovery.py   UDP broadcast: HELLO (servers), DISCOVER_GAME -> GAME_LIST (clients)
server/group_view.py  group view + room table (leader writes), versioned by (term, version),
                      ring/successor, load
server/election.py    LCR election on the ring sorted by UUID, skips unreachable successors
server/heartbeat.py   UDP heartbeats leader <-> servers every 1 s, crash after 3 misses
server/replication.py primary-backup per room: STATE_UPDATE/ACK (3 retries), PROMOTE on host
                      crash, leader repairs the room table (new host, new backup)
server/game.py        room game logic (no sockets), rejoin within 60 s, answers stored per guess
                      ID (no double counting), to_dict/from_dict for replication
server/main.py        TCP server: client game traffic + server messages (VIEW_UPDATE,
                      HOST_ROOM, ROOM_STATUS), room placement by the leader
client/main.py        CLI client: discovery, create via leader, game via the room's host
tests/                unit tests for game logic and group view
```

## Milestones

| # | Goal | Status |
|---|------|--------|
| M1 | Game on one server, several clients over TCP, ping/timeout | done |
| M2 | UDP broadcast discovery, leader keeps versioned group view + room table, rooms placed on the least-loaded server | done |
| M3 | Ring sorted by UUID, LCR election at start and on leader failure, heartbeats 1 s / 3 misses | done |
| M4 | Backup = host's ring successor, `STATE_UPDATE`/`ACK`, failover to the backup, automatic `REJOIN` within 60 s, resent guesses deduplicated | done |
| M5 | Status view, `--ip`, demo script (DEMO.md); rehearsal on 2+ machines | rehearsal open |

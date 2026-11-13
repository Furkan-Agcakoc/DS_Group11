"""Shared constants. Every node imports these, so all ports and timeouts live in one place."""

# --- Network -----------------------------------------------------------------
TCP_PORT = 6000             # default TCP port of a server (override with --port)
BROADCAST_PORT = 5972       # UDP broadcast discovery (HELLO, DISCOVER_GAME)
BROADCAST_ADDR = "255.255.255.255"  # override with --broadcast, e.g. 192.168.0.255
SERVER_TIMEOUT = 1.0        # timeout of a TCP request between servers

# --- Discovery ---------------------------------------------------------------------
DISCOVERY_TIMEOUT = 3.0     # a starting server that hears no leader for 3 s starts the group
HELLO_INTERVAL = 1.0        # HELLO is repeated every second until the leader answers
DISCOVER_RETRIES = 5        # client repeats DISCOVER_GAME every PING_INTERVAL, then gives up

# --- Failure detection ---------------------------------------------------------
HEARTBEAT_INTERVAL = 1.0    # M3: server <-> leader heartbeats (UDP)
HEARTBEAT_MISSES = 3        # M3: node counts as crashed after 3 missed heartbeats
PING_INTERVAL = 2.0         # client -> room host ping (TCP)
PING_TIMEOUT = 6.0          # host marks client as disconnected after 6 s without a ping
REJOIN_GRACE = 60.0         # offline player: seat and score are kept for 60 s
AFK_TIMEOUT = 30.0          # connected player without a guess for 30 s is removed from the game
REPLICATION_TIMEOUT = 1.0   # M4: wait for ACK from the backup
REPLICATION_RETRIES = 3     # M4: then the backup is reported as failed

# --- Game rules ------------------------------------------------------------------
MIN_PLAYERS = 2
MAX_PLAYERS = 6
ROUNDS = 5
NUMBER_MIN = 1
NUMBER_MAX = 100
MAX_ATTEMPTS = 10           # a player who needs more is out for this round
POINTS = [3, 2, 1]          # points for the 1st, 2nd and 3rd place of a round

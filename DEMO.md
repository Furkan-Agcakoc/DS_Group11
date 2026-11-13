# Demo-Leitfaden NumberRush (Gruppe 11)

15 Minuten Demo mit Fragen. Laut Folien: mindestens **zwei physische Rechner**, alles läuft
schon, wenn der Slot beginnt, **alle drei** beantworten Fragen.

## Vorbereitung (vor dem Termin, mindestens einmal komplett proben)

1. **Netz:** eigener Router oder Handy-Hotspot. Im **eduroam ist Broadcast blockiert**, dort
   funktioniert die Discovery nicht.
2. **Gleicher Code auf allen Laptops**, Python 3.10 oder neuer. Prüfen mit `python --version`.
3. **Firewall:** Beim ersten Start fragt Windows, ob Python ins Netz darf. Für **private
   Netzwerke** erlauben. Den Hotspot in Windows als *privates Netzwerk* einstellen.
   Ohne das kommen Broadcasts und TCP-Verbindungen vom anderen Laptop nicht an.
4. **Test:** `python -m unittest` muss auf jedem Laptop durchlaufen.
5. Findet die Discovery nichts:
   - Broadcast-Adresse des Hotspots angeben, bei **allen** Servern und Clients:
     `--broadcast 192.168.43.255` (an das eigene Netz anpassen, siehe `ipconfig`).
   - Zeigt der Server beim Start eine falsche IP an (VPN, VirtualBox, WSL), setzt
     `--ip <richtige IP>` sie fest.

## Getestetes Setup (13.11.2026)

- **freecampus funktioniert nicht:** Das Uni-WLAN lässt Broadcasts zwischen den Geräten nicht durch.
- **iPhone-Hotspot funktioniert**, mit `--broadcast 172.20.10.15` bei **allen** Servern und Clients.
  Auf dem iPhone „Kompatibilität maximieren“ einschalten.
- **Mac:** Der Befehl heißt `python3`. Ohne `--broadcast` scheitert macOS an `255.255.255.255`
  (`No route to host`).
- **Windows:** Das Hotspot-Netz auf „Privat“ stellen, NordVPN aus, Firewall-Regeln für die Ports
  6000–6010 (TCP und UDP) und 5972 (UDP) sind angelegt.

## Aufbau

Drei Laptops, jeder mit einem Server und einem Client. Alle Befehle im Ordner `DS_Group11`:

```
python -m server.main --port 6000 --broadcast 172.20.10.15
python -m client.main --name Furkan --broadcast 172.20.10.15
```

Auf dem Mac `python3` statt `python`. Die Befehle vorher in die Terminals tippen und erst in der
Demo mit Enter abschicken. Die Schrift im Terminal groß stellen (Strg + Mausrad, Mac: Cmd +).

In jedem Server-Fenster zeigt **Enter** den Status: Rolle, Ring, Raumtabelle, gehostete Räume
und Backup-Kopien. Damit lässt sich jeder Schritt gut erklären.

## Ablauf (ca. 9 Minuten, Rest für Fragen)

Kurz am Anfang (30 s): das Architekturdiagramm aus dem Report zeigen und sagen, welcher Schritt
welche Anforderung zeigt. Die Reihenfolge ist so gewählt, dass nach jedem Absturz noch genug
Server übrig sind.

| # | Szenario | zeigt |
|---|----------|-------|
| 1 | Start und Wahl | Architektur, Discovery (Server), Leader Election |
| 2 | Client findet Spiel, Spiel läuft | Discovery (Client), Architektur |
| 3 | Host stürzt mitten in der Runde ab | Fault Tolerance (Host) |
| 4 | Abgestürzter Server kommt zurück | Discovery (neuer Knoten), keine Neuwahl |
| 5 | Leader stürzt ab | Leader Election, Fault Tolerance (Leader) |
| 6 | Client stürzt ab | Fault Tolerance (Client) |

### 1. Discovery und Leader Election beim Start (1,5 min)
- Alle drei Server auf „3, 2, 1, los“ starten.
- Zeigen: `ELECTION started (system start), ring: …`, beim Server mit der höchsten UUID
  `own ID went around the ring -> I am the LEADER`, bei den anderen `new leader is …`.
- In einem Server Enter drücken: Ring sortiert nach UUID, Leader markiert, drei IPs.

### 2. Client findet das Spiel ohne IP (1,5 min)
- Spieler 1: `list` (UDP-Broadcast `DISCOVER_GAME`, nur der Leader antwortet), dann `create demo`.
- Leader-Log: `room … placed on …, backup …`. Der Leader wählt den Server mit den wenigsten
  Räumen als Host und dessen Ring-Nachfolger als Backup.
- Die anderen: `list`, dann `join <id>`. Einer: `start`. Ein paar Tipps spielen.

### 3. Host stürzt mitten in einer Runde ab (2,5 min) ← Höhepunkt
- Ein paar Tipps abgeben, dann beim Host (Server-Log: `hosting room …`) Enter drücken, um den
  Stand zu zeigen.
- Das Host-Fenster mit **Strg+C** beenden (Mac: Control+C).
- Zeigen:
  - Leader-Log nach etwa 3 s: `no heartbeat … crashed`, `host … crashed -> backup … is the new host`.
  - Backup-Log: `PROMOTED to host of room …`.
  - Bei den Clients: `Connection to the room host lost …`, dann `Back in room … (round x, your
    attempts this round: y, score: z)`. Punkte und Versuche sind erhalten.
- Weiterspielen.
- Ist der Host zufällig der Leader, passiert dasselbe plus eine Neuwahl. Dann Schritt 5 kürzer halten.

### 4. Der abgestürzte Server kommt zurück (1 min)
- Denselben Server-Befehl wieder starten.
- Zeigen: `joined group, leader is …`. Beim Leader: `server … joined from …`. Der Leader bleibt
  derselbe, keine Neuwahl, das Spiel läuft ungestört weiter.

### 5. Leader stürzt ab (1,5 min)
- Das Leader-Fenster mit Strg+C beenden.
- Zeigen: `no heartbeat from … -> crashed`, `ELECTION started (leader … failed)`, neuer
  `I am the LEADER now (term 2)`. Hat der alte Leader Räume gehostet, übernimmt ihr Backup.
- Client: `list` funktioniert weiter, jetzt antwortet der neue Leader.

### 6. Client stürzt ab und kommt zurück (1 min, wenn Zeit bleibt)
- Ein Client-Fenster schließen. Die anderen sehen den Spieler als `(offline)`, `list` zeigt
  `1 online / 2 players`. Die anderen spielen weiter, der Platz bleibt 60 s reserviert.
- Den Client mit **demselben Namen** neu starten, `list`, `join <id>`: `Back in room …` mit den
  alten Punkten.
- Optional AFK zeigen: Ein Spieler tippt 30 s lang nichts, obwohl er noch raten müsste. Er
  bekommt `Removed from the game for inactivity`, die anderen sehen `(kicked)`. Ein erneutes
  `join` wird abgelehnt.

## Mögliche Fragen und Antworten

**Warum hybrid?** Spieler ↔ Raum-Host ist Client-Server. Die Server untereinander sind
gleichberechtigte Peers in einem logischen Ring und wählen ihren Leader selbst.

**Wozu braucht ihr einen Leader?** Er pflegt die Group View und die Raumtabelle, beantwortet
Discovery-Anfragen der Clients, verteilt neue Räume und entscheidet bei einem Ausfall, welcher
Backup übernimmt. Ohne Leader könnten zwei Server gleichzeitig unterschiedliche Entscheidungen treffen.

**Warum LCR und nicht Bully?** Der Ring ergibt sich direkt aus der Group View, sortiert nach UUID.
Jeder Server kennt nur seinen Nachfolger. Nachrichten: im besten Fall O(n), im schlechtesten O(n²),
im Mittel O(n log n). Bully braucht dagegen Nachrichten an alle Server mit höherer ID.

**Wie läuft LCR genau?** Siehe `server/election.py`: E(mid, false) an den Nachfolger. Eine größere
mid wird weitergeleitet. Eine kleinere mid wird durch die eigene ersetzt, wenn der Server noch
kein Participant ist, sonst verworfen. Kommt die eigene ID zurück, ist der Server Leader und
schickt E(own, true) einmal um den Ring.

**Was ist, wenn zwei Server gleichzeitig eine Wahl starten?** Das ist für LCR kein Problem. Durch
das Participant-Flag werden kleinere IDs verworfen, und nur die größte UUID kommt einmal ganz herum.
Es gibt genau einen Leader.

**Warum keine Wahl, wenn ein Server beitritt?** Laufende Spiele sollen nicht gestört werden. Der
neue Server erfährt den Leader aus der Group View.

**Woher weiß ein Server, dass der Leader tot ist und nicht nur das Netz hakt?** Sicher wissen kann
er es nicht, das ist das Grundproblem in asynchronen Systemen. Wir nehmen nach 3 verpassten
Heartbeats (3 s) einen Absturz an. Hängt eine Wahl, startet sie nach einem Timeout neu.

**Wie verhindert ihr, dass alte Views neue überschreiben?** Jede View hat (term, version). Jeder neu
gewählte Leader erhöht den term. Ein Server übernimmt nur Views, die neuer sind als seine eigene.

**Warum geht kein bestätigter Tipp verloren?** Der Host schickt den neuen Zustand an den Backup und
wartet auf das ACK, bevor der Spieler „höher“, „tiefer“ oder „richtig“ bekommt (synchrone
Replikation). Alles, was ein Spieler gesehen hat, liegt also auch beim Backup.

**Und wenn der Host nach der Replikation, aber vor der Antwort abstürzt?** Der Client schickt den
Tipp nach dem Rejoin mit derselben Tipp-ID noch einmal. Der neue Host kennt die ID, antwortet erneut
und zählt den Tipp nicht doppelt.

**Warum TCP für Tipps und UDP für Heartbeats?** Tipps, Replikation und Election-Nachrichten dürfen
nicht verloren gehen oder in falscher Reihenfolge ankommen. Ein verlorener Heartbeat ist dagegen
harmlos, weil erst 3 fehlende zählen. Und Broadcast geht nur mit UDP.

**Warum entscheidet bei Gleichstand die Eingangsreihenfolge und nicht die Uhrzeit?** Im verteilten
System gibt es keine gemeinsame Uhr, die Uhren der Clients weichen voneinander ab. Der Host ist der
einzige Schreiber seines Raums, seine Eingangsreihenfolge ist also eindeutig.

**Warum Threads und kein Multiprocessing?** Die Arbeit besteht fast nur aus Warten auf das Netz
(I/O-bound). Threads teilen sich den Spielzustand einfach, mit Locks pro Raum.

**Was ist der Unterschied zwischen 6 s, 30 s und 60 s?**
- 6 s (Ping-Timeout): Lebt die **Verbindung** noch? Der Client pingt alle 2 s automatisch. Ohne
  das würde ein Laptop mit leerem Akku oder ohne WLAN nicht bemerkt, weil TCP dann gar nichts mehr
  meldet. Gilt in beide Richtungen: Der Host erkennt so tote Clients, der Client einen toten Host.
- 30 s (AFK): Spielt der **Mensch** noch? Wer verbunden ist, aber 30 s lang nichts rät, fliegt
  endgültig raus, damit er die Runde nicht aufhält.
- 60 s (Rejoin-Frist): Wie lange bleibt der **Platz** eines Spielers frei, dessen Verbindung weg
  ist? In dieser Zeit kommt er mit demselben Namen zurück.

**Warum kann ein AFK-Spieler nicht zurück, ein abgestürzter aber schon?** Ein Absturz ist nicht
die Schuld des Spielers, deshalb bleibt sein Platz frei. AFK ist eine Spielregel gegen das Aufhalten.
Der Zustand `kicked` wird an den Backup repliziert, damit er auch nach einem Host-Absturz gilt.

**Welche Grenzen hat euer System?**
- Fallen Host und Backup eines Raums gleichzeitig aus, ist der Raum verloren (nur ein Backup).
- Startet ein Server genau während einer Wahl, kann er eine eigene Gruppe bilden.
- Bei einer Netzwerkpartition (Split-Brain) kann es zwei Leader geben.
- Byzantinische Fehler werden nicht behandelt. Zum Beispiel kann jemand, der den Namen eines
  Offline-Spielers kennt, dessen Platz übernehmen, weil es keine Passwörter gibt.
- Broadcast funktioniert nur im selben Subnetz.

## Code verstehen: Lernplan

Ziel: Jeder kann zu jeder Log-Zeile der Demo sagen, welche Funktion sie schreibt und warum.
Jede Datei beginnt mit einem Erklärtext, der den Ablauf beschreibt. Den immer zuerst lesen.

### Schritt 1: Grundlagen (alle zusammen, ca. 1 Stunde)
1. `common/config.py`: alle Zahlen aus dem Report (1 s, 3 Fehlversuche, 2 s, 6 s, 60 s).
2. `common/protocol.py`: Nachrichtenformat (JSON mit `type`, `sender`, `msg_id`), warum ein
   Längenpräfix bei TCP nötig ist, `request()` für kurze Server-zu-Server-Verbindungen.
3. `server/game.py`: die Spielregeln ohne Netzwerk. `guess()`, `finish_round()`, `to_dict()`.

### Schritt 2: Eine Nachricht verfolgen (alle zusammen, ca. 1 Stunde)
Einen Tipp durch das ganze System verfolgen, Datei für Datei:
`client/main.py` `command()` → `GUESS` über TCP → `server/main.py` `dispatch()` → `on_guess()`
→ `room.guess()` → `_replicate()` → `replication.py` `replicate()` (STATE_UPDATE, wartet auf ACK)
→ `FEEDBACK` an den Client → `client/main.py` `handle()`.
Wer das erklären kann, versteht die Architektur.

### Schritt 3: Jeder übernimmt einen Teil (allein, je 2–3 Stunden)
| Person | Dateien | Muss erklären können |
|--------|---------|----------------------|
| Furkan | `discovery.py`, `group_view.py` | HELLO und DISCOVER_GAME, wer antwortet, wie die View verteilt wird, warum (term, version) |
| Marvin | `election.py`, `heartbeat.py` | die LCR-Regeln Zeile für Zeile, wann eine Wahl startet, wie ein Absturz erkannt wird |
| Mehmet-Akif | `replication.py`, `client/main.py` | Replikation mit ACK, PROMOTE, `repair_rooms()`, Rejoin, warum kein Tipp doppelt zählt |

Die LCR-Regeln in `election.py` mit den Folien aus „06 Leader election“ (S. 16–17) vergleichen,
sie sind identisch aufgebaut.

### Schritt 4: Sich gegenseitig erklären (zusammen, ca. 2 Stunden)
- Jeder erklärt seinen Teil den anderen, ohne Spickzettel. Die anderen stellen Fragen.
- Danach rotieren: Jeder erklärt einen Teil, den er **nicht** bearbeitet hat.
- Für Szenario 3 (Host-Absturz) und 5 (Leader-Absturz) einen Ablauf auf Papier zeichnen:
  Wer schickt wem welche Nachricht, in welcher Reihenfolge.

### Schritt 5: Logs und Code verbinden
- Die Demo einmal laufen lassen und zu jeder Log-Zeile in VS Code die Stelle suchen
  (Strg+Shift+F, z. B. nach `PROMOTED to host` suchen).
- Kleine Experimente: In `config.py` `HEARTBEAT_MISSES = 10` setzen und beobachten, dass die
  Erkennung jetzt 10 s dauert. Danach zurücksetzen.

### Schritt 6: Generalprobe (mindestens zweimal)
- Komplette Demo mit Stoppuhr, auf drei Laptops im Hotspot.
- Einer spielt den Prof und stellt Fragen aus der Liste oben, auch zu Teilen, die der Gefragte
  nicht selbst bearbeitet hat.

## Notfallplan
- **Discovery findet nichts:** prüfen, ob alle im Hotspot sind, `--broadcast 172.20.10.15` überall,
  NordVPN aus, Netz auf „Privat“.
- **Zweites Handy** als Ersatz-Hotspot bereithalten. Bei einem Android-Hotspot ist die
  Broadcast-Adresse eine andere: `ipconfig` prüfen.
- **Bildschirmaufnahme** einer erfolgreichen Generalprobe machen. Sie ersetzt die Live-Demo nicht,
  hilft aber zu zeigen, dass es funktioniert, falls das Netz vor Ort streikt.

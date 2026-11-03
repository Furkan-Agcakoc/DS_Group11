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

## Aufbau

| Laptop | Fenster |
|--------|---------|
| A | Server 1 (`--port 6000`), Client Alice |
| B | Server 2 (`--port 6000`), Server 3 (`--port 6001`), Client Bob |
| C (optional) | Client Carol, oder später ein neuer Server |

Alle Befehle im Ordner `DS_Group11`:

```
python -m server.main --port 6000
python -m client.main --name Alice
```

In jedem Server-Fenster zeigt **Enter** den Status: Rolle, Ring, Raumtabelle, gehostete Räume
und Backup-Kopien. Damit lässt sich jeder Schritt gut erklären.

## Ablauf (ca. 10 Minuten, Rest für Fragen)

### 1. Discovery und Leader Election beim Start (2 min)
- Server 1 bis 3 innerhalb von 3 Sekunden starten.
- Zeigen: `no leader answered within 3 s -> starting an election`, `ELECTION started …, ring: …`,
  `I am the LEADER now (term 1)` beim Server mit der höchsten UUID, `new leader is …` bei den anderen.
- In einem Server Enter drücken: Ring, sortiert nach UUID, der Leader ist markiert.

### 2. Client findet das Spiel ohne IP (1 min)
- Alice: `list` (UDP-Broadcast `DISCOVER_GAME`, nur der Leader antwortet), dann `create demo`.
- Leader-Log: `room … placed on …, backup …`. Der Leader wählt den Server mit den wenigsten
  Räumen als Host und dessen Ring-Nachfolger als Backup.
- Bob: `list`, dann `join <id>`. Alice: `start`. Ein paar Tipps spielen.

### 3. Neuer Server tritt bei, ohne Neuwahl (1 min)
- Auf Laptop C (oder als weiteres Fenster) einen Server starten.
- Zeigen: `joined group, leader is …`. Der Leader bleibt derselbe, das Spiel läuft ungestört weiter.

### 4. Host stürzt mitten in einer Runde ab (3 min) ← Höhepunkt
- Vorher in Alices Raum ein paar Tipps abgeben, dann beim Host (Server-Log: `hosting room …`)
  Enter drücken, um den Stand zu zeigen.
- Das Host-Fenster mit **Strg+C schließen**.
- Zeigen:
  - Leader-Log nach etwa 3 s: `no heartbeat … crashed`, `host … crashed -> backup … is the new host`.
  - Backup-Log: `PROMOTED to host of room …`.
  - Bei den Clients: `Connection to the room host lost …`, dann `Back in room … (round x, your
    attempts this round: y, score: z)`. Punkte und Versuche sind erhalten.
- Weiterspielen.

### 5. Leader stürzt ab (2 min)
- Das Leader-Fenster mit Strg+C schließen.
- Zeigen: `no heartbeat from … -> crashed`, `ELECTION started (leader … failed)`, neuer
  `I am the LEADER now (term 2)`. Hat der alte Leader Räume gehostet, übernimmt ihr Backup.
- Client: `list` funktioniert weiter, jetzt antwortet der neue Leader.

### 6. Client stürzt ab (1 min, wenn Zeit bleibt)
- Ein Client-Fenster schließen. Der Host markiert den Spieler nach spätestens 6 s als `offline`.
  Die anderen spielen weiter, der Platz bleibt 60 s reserviert.

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

**Welche Grenzen hat euer System?**
- Fallen Host und Backup eines Raums gleichzeitig aus, ist der Raum verloren (nur ein Backup).
- Startet ein Server genau während einer Wahl, kann er eine eigene Gruppe bilden.
- Bei einer Netzwerkpartition (Split-Brain) kann es zwei Leader geben.
- Byzantinische Fehler werden nicht behandelt.
- Broadcast funktioniert nur im selben Subnetz.

## Wer erklärt was (Vorschlag)
- **Discovery, Group View, Raumtabelle:** `discovery.py`, `group_view.py`
- **Ring, LCR, Heartbeats:** `election.py`, `heartbeat.py`
- **Spiel, Client, Replikation, Rejoin:** `game.py`, `client/main.py`, `replication.py`

Trotzdem sollte **jeder den ganzen Ablauf** von Punkt 4 und 5 erklären können.

# Geplante Änderung: Rejoin über den Namen, AFK-Rauswurf, bessere Anzeige

**Status: umgesetzt und getestet (13.11.2026).** Dieses Dokument beschreibt die Logik, so wie sie
jetzt im Code steht.

## 1. Die drei Zeitgrenzen im Überblick

Nach der Änderung gibt es drei Zeitgrenzen für Spieler. Sie messen verschiedene Dinge:

| Grenze | Wert | Frage, die sie beantwortet | Was danach passiert |
|--------|------|----------------------------|---------------------|
| Ping-Timeout (gibt es schon) | 6 s | Lebt die **Verbindung** zum Client noch? | Spieler wird `offline` |
| Rejoin-Frist (gibt es schon) | 60 s | Wie lange bleibt der **Platz** eines Offline-Spielers reserviert? | Platz verfällt, kein Rejoin mehr |
| AFK-Timeout (neu) | 30 s | Wie lange darf ein **verbundener** Spieler in einer Runde nichts tun? | Spieler fliegt endgültig aus dem Spiel |

### Wozu die 6 Sekunden?
Der Client schickt im Hintergrund alle 2 s automatisch ein `PING` an den Host-Server, auch wenn der
Spieler gerade nichts tippt. Der Server antwortet mit `PONG`. Kommt 6 s lang nichts (3 verpasste
Pings), gilt die Verbindung als tot.

Das ist nötig, weil TCP allein einen stummen Ausfall **nicht** bemerkt:
- Wird ein Programm mit Strg+C beendet, schickt das Betriebssystem noch ein „Verbindung zu“.
  Das merkt die Gegenseite sofort. Deshalb ging es in unseren Tests immer so schnell.
- Ist aber der **Akku leer, der Laptop zugeklappt oder das WLAN weg**, kommt gar nichts mehr an.
  Ohne Ping würde der Server minutenlang glauben, der Spieler sei noch da, und die Runde würde auf
  ihn warten.

Die Regel wirkt in **beide Richtungen**:
- Der **Host-Server** erkennt nach 6 s ohne `PING`, dass ein Client weg ist, und markiert ihn als `offline`.
- Der **Client** erkennt nach 6 s ohne `PONG`, dass der Host-Server weg ist, und sucht den neuen Host
  (automatischer Rejoin).

Der Ping misst nur, ob der **Rechner und das Programm** noch erreichbar sind. Ob der **Mensch** davor
sitzt, misst er nicht. Dafür ist der neue AFK-Timeout da.

## 2. Rejoin über den Namen

### Problem heute
Der Rejoin funktioniert nur automatisch über die Spieler-ID, die der laufende Client im Speicher hat.
Nach `leave`, nach dem Schließen des Fensters oder nach einem echten Absturz ist die ID weg. Der
Platz wird zwar 60 s freigehalten, aber niemand kann ihn zurückbekommen. Das widerspricht dem Report
(„Client crash: seat and score are kept for 60 s for a rejoin“).

### Neue Regel für `join <raum-id>` mit dem eigenen Namen
| Zustand des Raums | Spieler mit diesem Namen im Raum? | Ergebnis |
|-------------------|-----------------------------------|----------|
| `waiting` (noch nicht gestartet) | nein | normaler Beitritt (wie heute) |
| `waiting` | ja | Fehler: Name schon vergeben (wie heute) |
| `playing` | nein | Fehler: Spiel läuft schon (wie heute) |
| `playing` | ja, **offline**, Frist noch nicht abgelaufen | **Rejoin**: Platz, Punkte und Versuche zurück |
| `playing` | ja, aber **online** | Fehler: Name ist gerade im Spiel (niemand darf einen aktiven Spieler übernehmen) |
| `playing` | ja, offline, aber **Frist abgelaufen** | Fehler: Platz verfallen |
| `playing` | ja, aber **wegen AFK rausgeflogen** | Fehler: aus dem Spiel entfernt |

Der automatische Rejoin nach einem Absturz des Host-Servers bleibt wie er ist (über die Spieler-ID).

### Was das für die Fälle bedeutet
| Situation | Heute | Neu |
|-----------|-------|-----|
| Host-Server stürzt ab, Client läuft weiter | automatischer Rejoin | unverändert |
| `leave` während des Spiels | kein Weg zurück | innerhalb 60 s mit `join <id>` und gleichem Namen zurück |
| Client-Fenster geschlossen / Absturz | kein Weg zurück | Client neu starten (gleicher Name), `list`, `join <id>` |
| Länger als 60 s weg | kein Weg zurück | unverändert: kein Weg zurück |

### Bekannte Schwäche
Es gibt keine Passwörter: Wer den Namen eines **Offline**-Spielers kennt, kann dessen Platz
übernehmen. Für ein Spiel unter Freunden ist das in Ordnung. Im Report sind Byzantine Faults
(böswillige Teilnehmer) ausdrücklich nicht abgedeckt, das passt dazu.

## 3. AFK-Rauswurf (neu)

### Ziel
Eine Runde endet erst, wenn alle verbundenen Spieler die Zahl gefunden oder ihre 10 Versuche
verbraucht haben. Ein Spieler, der verbunden ist, aber nichts tut, hält heute die Runde für alle auf.

### Regel
- **Wer wird beobachtet?** Nur Spieler, die in der laufenden Runde noch raten müssen: verbunden,
  Zahl noch nicht gefunden, noch Versuche übrig.
- **Wer wird nicht beobachtet?**
  - Spieler, die schon fertig sind und auf die anderen warten.
  - Spieler, die offline sind (für die gilt die 60-s-Rejoin-Frist).
  - Alle Spieler vor dem Spielstart (`waiting`).
- **Was zählt als Aktivität?** Nur ein abgeschickter Tipp (`GUESS`). Das automatische `PING` zählt
  **nicht**, sonst würde ein offenes, aber unbenutztes Fenster ewig als aktiv gelten.
- **Wann startet die Uhr?** Bei Rundenbeginn und nach jedem eigenen Tipp neu, außerdem nach einem
  Rejoin.
- **Nach 30 s ohne Tipp:**
  1. Der Host-Server schickt dem Spieler `KICKED` mit dem Grund „inactive for 30 s“.
  2. Der Spieler wird als `kicked` markiert. Er verliert den Platz endgültig, ein Rejoin über den
     Namen ist nicht möglich. Seine bisherigen Punkte bleiben in der Wertung stehen.
  3. Der Server schließt die Verbindung. Der Client zeigt „Removed for inactivity“ und kehrt in die
     Lobby zurück. Wegen `KICKED` versucht er **keinen** automatischen Rejoin.
  4. Die anderen sehen ihn im Raum als `(kicked)`.
  5. War er der Letzte, auf den die Runde gewartet hat, endet die Runde sofort.
- **Der neue Zustand wird an den Backup repliziert** wie jede andere Änderung, damit nach einem
  Absturz des Host-Servers niemand plötzlich wieder im Spiel ist.
- **Bleibt niemand mehr übrig** (alle offline oder rausgeflogen), endet das Spiel (`GAME_OVER`).

### Beispiel
```
0 s   Runde 2 beginnt. Alice, Bob und Carol müssen raten.
5 s   Alice tippt (Uhr von Alice neu)
12 s  Alice hat die Zahl gefunden -> fertig, wird nicht mehr beobachtet
15 s  Bob tippt (Uhr von Bob neu)
22 s  Bob hat die Zahl gefunden -> fertig
30 s  Carol hat seit Rundenbeginn nichts getippt -> KICKED
      Carol war die Letzte, auf die gewartet wurde -> Runde 2 endet sofort
```

### Offline ist nicht dasselbe wie AFK
| Grund | Zustand | Mit gleichem Namen zurück? |
|-------|---------|----------------------------|
| Absturz, Fenster geschlossen, WLAN weg, `leave` | `offline` | **ja**, innerhalb von 60 s |
| 30 s nichts getippt, obwohl verbunden | `kicked` | **nein**, endgültig |

### Zusammenspiel mit dem Absturz des Host-Servers
Die AFK-Uhren laufen nur lokal auf dem Host-Server und werden nicht repliziert, genau wie die
60-s-Rejoin-Frist. Übernimmt der Backup einen Raum, starten alle Uhren neu. Nach einem Absturz
bekommt also jeder wieder volle 30 s. Das ist gewollt, weil die Spieler gerade erst zurückkommen.
Der Zustand `kicked` selbst wird dagegen repliziert: Wer rausgeflogen ist, bleibt es auch nach
einem Absturz des Host-Servers.

## 4. Bessere Anzeige bei `list`
```
Leader: 172.20.10.3:6000
  510d2c  Piçello    2 online / 3 players  playing  host 172.20.10.3:6000
  c42e37  amkiello   0 online / 0 players  waiting  host 172.20.10.8:6000
```
- `players` zählt alle Plätze, auch Spieler, die offline oder rausgeflogen sind (sie haben noch
  Punkte in der Wertung).
- `online` zählt die gerade verbundenen Spieler.
- Die Leader-Zeile zeigt, welcher Server auf die Discovery geantwortet hat. Nach einem Absturz des
  Leaders sieht man dort den neuen.

## 5. Was sich wo ändern würde
| Datei | Änderung |
|-------|----------|
| `common/config.py` | neue Konstante `AFK_TIMEOUT = 30.0` |
| `common/protocol.py` | neue Nachricht `KICKED` |
| `server/game.py` | Rejoin über den Namen, Zustand `kicked`, AFK-Uhr pro Spieler, Online-Zähler |
| `server/main.py` | `JOIN` entscheidet zwischen Beitritt und Rejoin, ein Thread prüft jede Sekunde die AFK-Uhren |
| `server/group_view.py` | Online-Zähler in der Raumtabelle |
| `client/main.py` | `KICKED` anzeigen ohne Auto-Rejoin, Leader und Online-Zähler bei `list` |
| `tests/` | Tests für Rejoin über den Namen und den AFK-Rauswurf |
| `DEMO.md` | Szenario 6 (Client stürzt ab) mit Rejoin über den Namen; Fragen zu den drei Zeitgrenzen |

Der Report muss nicht geändert werden: Die Recovery-Aussage zum Client-Absturz stimmt danach erst
richtig. Der AFK-Rauswurf ist eine Spielregel und kein Teil der verteilten Anforderungen.

## 6. Entscheidungen der Gruppe (13.11.2026)
1. **AFK-Folge:** Ein AFK-Spieler fliegt **komplett aus dem Spiel**.
2. **AFK-Zeit:** **30 s** ohne Tipp.
3. **Rejoin:** Nach einem AFK-Rauswurf **nicht** möglich. Nach einem Absturz oder Verbindungsverlust
   (`offline`) **schon**, mit gleichem Namen innerhalb von 60 s.

---
name: multiplayer-testing
description: >
  Test replication and multiplayer gameplay with pie_test in ONE editor process
  (multi-client PIE): start a listen server or a dedicated server with N clients,
  drive one client, and assert what the server and the other clients see. Use for
  shared worlds, co-op or PvP modes, "does this replicate?", and join/leave bugs.
---

# Multiplayer testing: multi-client PIE

Everything runs inside the one editor process (Play settings "Run Under One Process"). pie_test never launches extra game or
server processes, so a 2-client test costs about as much RAM as two PIE viewports. Keep `clients` small (2 or 3; the limit is 8).

## Start a session
```
pie_test(net_mode="listen_server", clients=2, ...)   # host (also a player) + 2 remote clients = 3 players
pie_test(net_mode="client", clients=2, ...)          # Play As Client: 2 clients + an in-process dedicated server (no window)
```
Leave `net_mode` out to keep the editor's saved Play settings (the old behaviour). `net_mode` only changes this session's copy
of the settings, so the user's own Play options are not touched. pie_test waits until every client has connected (server
connection up, and the server's PlayerController for it replicated) and puts `worlds` in the result:

| target | world |
|---|---|
| `server` | the listen server (host player) or the dedicated server |
| `client:1` ... `client:N` | the client worlds, in PIE instance order |

`pie_worlds()` shows the same list in a running session: players, connected, pawn, has_viewport, all_connected.

## Target a world
Every step and assertion takes `"world"` (or `"net_role"`). Without it, the target is the editor's PlayWorld, which is
ambiguous with several clients, so always set it in multiplayer tests.

- `{"spawn": "/Game/BP_Car", "label": "Car", "world": "server", "possess_for": "client:1"}`: spawn on the server (only server
  spawns replicate) and hand the pawn to client 1's player.
- `{"possess": "Car", "world": "client:1"}`: client 1's player possesses Car. Possession is server-authoritative, so it goes
  through the server's controller for that client, and `Car` is looked up in the server world.
- `{"hold": "W", "duration_s": 2, "world": "client:1"}`: input on client 1's own controller. It goes through client prediction
  and server RPCs, like a real remote player's input.
- `{"check": [...], "world": "client:2"}`, or `"world"` on a single assertion: pie_assert runs in that world. There, `@player` is
  that client's local pawn, and the Python scope adds `net_role`, `pie_world("server")` and `replica(actor_or_label, "client:2")`.
- `{"capture": "client2_view", "world": "client:2"}`: that client's viewport. A dedicated server has no viewport.
- `{"wait_clients": true, "timeout_s": 30}`, or a number of clients: wait for joins in the middle of a run.

Outside pie_test, `spawn_in_pie`, `pie_possess`, `pie_input` and `viewport_capture` take the same `world` argument.

## Replication assertions
These are checked again every 0.1 s until they pass or `within_s` runs out (default 5 s). Their results include `tries` and
`after_s`.

```json
{"type": "replicated", "label": "Car", "within_s": 5}
{"type": "replicated", "class": "BP_Pickup", "clients": [2], "min": 3}
{"type": "replicated_property", "target": "Car", "property": "location", "tolerance": 50}
{"type": "replicated_property", "target": "Car", "property": "Health", "op": "=="}
{"type": "player_count"}                 // server players == expected (listen: clients + 1); or "value", "op"
{"type": "clients_connected"}            // every requested client is connected
```
- `replicated`: each matching actor in the source world (`"source"`, default `server`) has a copy in each client world.
- `replicated_property`: each copy's value is within `tolerance` of the server's (default 1.0; `"op": "=="` for an exact match).
  The value is `location`, `rotation`, `velocity` or a property path (`"Health"`, `"CharacterMovement.MaxWalkSpeed"`).
  `actual` holds every world's value, which shows which client is stale.
- How copies are matched: by net GUID first (the same id on the server and every client), then by name (for level-placed
  actors), then by being the only actor of that class. Labels are not replicated: on a client, refer to a spawned actor by
  its replica, not by the label you gave it on the server.

## Example: 2 clients join, client 1 drives, client 2 sees the car move
```
pie_test(net_mode="listen_server", clients=2, duration_s=1, steps=[
  {"wait_clients": true},
  {"spawn": "/Game/Vehicles/BP_Car", "label": "Car", "location": [0, 0, 100], "world": "server",
   "possess_for": "client:1"},
  {"check": [{"type": "replicated", "label": "Car", "within_s": 5}]},
  {"hold": "W", "duration_s": 3, "world": "client:1"},
  {"capture": "client2_sees_car", "world": "client:2"}
], assertions=[
  {"name": "car moved on the server", "type": "python", "world": "server",
   "expr": "dist(find('Car'), [0, 0, 100]) > 300"},
  {"name": "client 2 saw it move", "type": "python", "world": "server",
   "expr": "dist(replica(find('Car'), 'client:2'), [0, 0, 100]) > 300"},
  {"name": "client 2 in sync", "type": "replicated_property", "target": "Car", "property": "location",
   "tolerance": 100, "clients": [2]},
  {"type": "player_count"},
  {"type": "no_errors"}
])
```
How to read the result:
- Step 2's `actual.replicas` names Car's copy on each client and how it was matched (`net_id`).
- "car moved on the server" proves that client 1's input reached the server, through possession and the server RPCs.
- "client 2 saw it move" and "client 2 in sync" prove that the movement replicated out to the other client. `actual` lists
  the server and client:2 locations side by side.
- The capture is client 2's view.

## Shared free-roam world vs. instances
- Shared world (friends roaming together): use `net_mode="listen_server"` or `"client"` with 2 or 3 clients. Assert that
  every player sees the others (`{"type": "replicated", "class": "BP_PlayerCharacter"}`), and that world events spawned on
  the server reach everyone.
- Isolated instance modes on the same map: start each instance as its own pie_test, one at a time. Two PIE sessions in one
  editor at once are not supported, and pie_test refuses to start while PIE is running.

## Limits
- Under Iris replication (`net.Iris.UseIrisReplication=1`), net GUIDs are not available. Copies are then matched by name or
  by being the only actor of their class, so give spawned actors distinct classes or check level-placed ones.
- Network emulation (lag, packet loss) uses the editor's Play settings. pie_test does not set it yet.
- Each extra client is a full game world in the same process. Watch `frame_stats` and memory.

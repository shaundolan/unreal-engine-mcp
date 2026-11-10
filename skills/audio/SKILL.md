---
name: audio
description: >
  Procedural and sample-based game audio: build MetaSound sources with metasound_apply (oscillators, noise,
  filters, envelopes, Wave Player, mixers, math, triggers, exposed inputs), read them with metasound_export,
  find node classes with metasound_nodes, and make simple SoundCues with sound_cue_apply. Recipes for an engine
  loop driven by RPM, a footstep randomizer, a UI click, an ambience mixer and an alarm.
---

# Audio

The loop is **find nodes → `metasound_apply` → read `graph` / `metasound_export` → play it**.
`metasound_apply` creates the asset if it doesn't exist. A new source already has:
- graph input `OnPlay` (trigger, fires when the sound starts);
- graph output `OnFinished` (trigger; only one-shots have it: `one_shot=false` makes a looping source that plays until stopped);
- audio output `Audio` (mono) or `Left` / `Right` (`format="stereo"`).

A failed create writes nothing. Edits to an existing MetaSound apply op by op, and the reply says which op failed.

## Finding nodes and pins
`metasound_nodes("filter")` lists classes with their pins as `Name:Type`. `add_node` takes the short name (`"Sine"`,
`"Biquad Filter"`, `"ADSR Envelope"`) or the full one (`"UE.Sine.Audio"`). Nodes with several variants (math, envelopes,
Map Range, Wave Player) default to `Float`, then `Audio`. Pass `"variant"`: `"Audio"`, `"Mono"`, `"Stereo"`, `"Int32"`, `"Audio by Float"`.

Pins match by name, ignoring case and spaces. Types must match exactly: a `Float` is not an `Audio` buffer.
Multiply audio by a float with `Multiply` + `"variant":"Audio by Float"`. Common pins:

| Node | Inputs | Outputs |
|---|---|---|
| Sine / Saw / Square / Triangle | Enabled, Bi Polar, Frequency, Modulation, Sync, Phase Offset, Glide | Audio |
| Noise | Seed, Type | Audio |
| Biquad Filter / Ladder Filter / State Variable Filter | In, Cutoff Frequency, Bandwidth / Resonance | Out |
| ADSR Envelope / AD Envelope | Trigger Attack, Trigger Release, Attack Time, Decay Time, Sustain Level, Release Time | Out Envelope, On Done |
| Wave Player (Mono / Stereo) | Play, Stop, Wave Asset, Start Time, Pitch Shift, Loop | On Play, On Finished, Out Mono / Out Left, Out Right |
| Add / Multiply (Float) | PrimaryOperand, AdditionalOperands | Out |
| Map Range (Float) | In, In Range A/B, Out Range A/B, Clamped | Out Value |
| Trigger Repeat / Trigger Delay / Random Get | Start, Stop, Period / In, Delay Time / Next, In Array | RepeatOut / Out / Value |

These names come from the engine source. If one doesn't match, the error lists the node's real pins.

## Ops
```json
{"op":"add_input","name":"RPM","type":"Float","default":1000}      // set from gameplay: Set Float Parameter "RPM"
{"op":"add_node","id":"osc","class":"Saw","inputs":{"Frequency":110}}
{"op":"connect","from":"graph.RPM","to":"map.In"}                  // graph input -> node
{"op":"connect","from":"osc.Audio","to":"graph.Audio"}             // node -> graph output (stereo: graph.Left/Right)
{"op":"connect","from":"graph.OnPlay","to":"env.Trigger Attack"}
{"op":"set","node":"osc","inputs":{"Frequency":220}}   {"op":"set_input_default","name":"RPM","value":900}
{"op":"disconnect","to":"osc.Frequency"}   {"op":"remove_node","node":"<id>"}   {"op":"add_interface","name":"UE.Attenuation"}
```
Input types: `Float`, `Int32`, `Bool`, `Trigger`, `Time`, `String`, `WaveAsset`, `Audio`, `WaveAsset:Array`. Wave defaults take an asset path.

## Recipes
**Engine loop with an RPM input** (looping, mono):
```json
metasound_apply(asset="/Game/Audio/MS_Engine", one_shot=false, operations=[
  {"op":"add_input","name":"RPM","type":"Float","default":1000},
  {"op":"add_node","id":"map","class":"Map Range","inputs":{"In Range A":800,"In Range B":7000,"Out Range A":40,"Out Range B":220,"Clamped":true}},
  {"op":"add_node","id":"saw","class":"Saw"},
  {"op":"add_node","id":"sq","class":"Square","inputs":{"Pulse Width":0.3}},
  {"op":"add_node","id":"half","class":"Multiply","inputs":{"AdditionalOperands":0.5}},
  {"op":"add_node","id":"mix","class":"Add","variant":"Audio"},
  {"op":"add_node","id":"lp","class":"Ladder Filter","inputs":{"Cutoff Frequency":1200,"Resonance":2}},
  {"op":"connect","from":"graph.RPM","to":"map.In"},
  {"op":"connect","from":"map.Out Value","to":"saw.Frequency"},
  {"op":"connect","from":"map.Out Value","to":"half.PrimaryOperand"},
  {"op":"connect","from":"half.Out","to":"sq.Frequency"},
  {"op":"connect","from":"saw.Audio","to":"mix.PrimaryOperand"},
  {"op":"connect","from":"sq.Audio","to":"mix.AdditionalOperands"},
  {"op":"connect","from":"mix.Out","to":"lp.In"},
  {"op":"connect","from":"lp.Out","to":"graph.Audio"}])
```
Drive it from a vehicle Blueprint: an Audio component with this sound, then each Tick (or on a timer) call
`SetFloatParameter(InName="RPM", InFloat=<engine rpm>)` on the component.

**Footstep randomizer**: if you have footstep waves, a SoundCue is the cheapest:
`sound_cue_apply(asset="/Game/Audio/SC_Footstep", waves=[...4 waves...], pitch_range=[0.92,1.08], volume_range=[0.85,1.0])`.
This gives a Random node (no repeats) under a Modulator. In MetaSound: a `WaveAsset:Array` input, then `Random Get` (variant `WaveAsset`, Next ← `graph.OnPlay`),
then `Wave Player` (Mono) Wave Asset; `Out Mono` → `graph.Audio`, `On Finished` → `graph.OnFinished`, and a random `Pitch Shift`
from `Random Get` (Float, range −1..1 semitones).

**UI click** (one-shot, procedural, no assets): `Sine` 2200 Hz + `Noise` → `Add` (Audio) → `Multiply` (`Audio by Float`)
by an `AD Envelope` (Float, Attack Time 0.001, Decay Time 0.04) whose `Trigger Attack` ← `graph.OnPlay` and whose
`On Done` → `graph.OnFinished`.

**Ambience mixer** (looping, stereo): two or three `Wave Player` (Stereo, `Loop`=true) nodes started from `graph.OnPlay`.
Each `Out Left` / `Out Right` goes through `Multiply` (`Audio by Float`) with a Float graph input (`WindLevel`, `BirdsLevel`, ...),
then an `Add` (Audio) per channel → `graph.Left` / `graph.Right`. Gameplay fades layers with Set Float Parameter.

**Alarm** (looping): `Square` whose Frequency comes from `LFO` (Float output, Frequency 2, Min 600, Max 900)
→ `Biquad Filter` (Cutoff Frequency 3000) → `Multiply` (`Audio by Float`, AdditionalOperands 0.4) → `graph.Audio`. Add
a `Bool` input `Armed` routed to the oscillator's `Enabled` to mute it without stopping the sound.

## Check your work
- `metasound_export(asset)` lists nodes (class, kind, pins, defaults), graph inputs with defaults, and edges.
  Every audio path should end at an `Output` node (`UE.OutputFormat...`), and every input you added should have an edge.
- `metasound_apply(..., audition=true)` plays the result in the editor. Offscreen editors have no audio device, so judge by the export.
- In game: an `AudioComponent` or `SpawnSound2D` with the MetaSound; parameters by the input names you gave.

## Gotchas
- No implicit conversions. `Float` → `Time` needs the converter node: search `metasound_nodes("time")`.
- An input pin takes one connection. Sum several sources with `Add` (Audio) or a `Mixer` node first.
- `Map Range` has its own namespace (`MapRange.MapRange.Float`); the short name works.
- Large graphs: build in one call with your own `id`s. Later calls reference nodes by the ids in the result (`ids`) or by `metasound_export` node ids.

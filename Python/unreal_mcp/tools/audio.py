"""Audio: MetaSound sources built with the MetaSound Builder API, and SoundCue basics."""

from __future__ import annotations

from typing import Any

from . import call

_LARGE = 300.0


def _finish(r: dict[str, Any]) -> dict[str, Any]:
    if r.get("success") and "ok" in r:
        r["success"] = bool(r.pop("ok"))
        if not r["success"] and not r.get("error"):
            r["error"] = (
                "; ".join(f"op {f['index']} ({f['op']}): {f['error']}" for f in r.get("failed", [])[:8]) or "failed"
            )
    return r


def register(mcp, get_conn):
    @mcp.tool()
    def metasound_apply(
        asset: str,
        operations: list[dict[str, Any]],
        format: str = "mono",
        one_shot: bool = True,
        save: bool = True,
        audition: bool = False,
    ) -> dict[str, Any]:
        """Create (if missing) or edit a MetaSound Source with the MetaSound Builder API.

        New sources get the standard interface: graph inputs "OnPlay" (trigger), outputs "OnFinished"
        (one_shot=true only; one_shot=false makes a looping source that plays until stopped) and audio
        "Audio" (mono) or "Left"/"Right" (format="stereo"). Ops, in order:
          {"op":"add_node","id":"osc","class":"Sine","inputs":{"Frequency":220}}
              class: a node name ("Sine", "Saw", "Noise", "Biquad Filter", "Ladder Filter", "ADSR Envelope",
              "AD Envelope", "Wave Player" + "variant":"Mono"/"Stereo", "Mixer", "Multiply", "Add", "Map Range",
              "Random Get", "Trigger Repeat", "Trigger Delay", "Trigger Sequence", ...) or a full class name
              ("UE.Sine.Audio"). Find names and pin names with metasound_nodes.
          {"op":"add_input","name":"RPM","type":"Float","default":1000}   (Float Int32 Bool Trigger Time
              String WaveAsset Audio, WaveAsset:Array): a parameter set at runtime (Set Float Parameter)
          {"op":"add_output","name":"Level","type":"Float"}
          {"op":"connect","from":"graph.OnPlay","to":"env.Trigger Attack"}
          {"op":"connect","from":"graph.RPM","to":"map.Value"}
          {"op":"connect","from":"osc.Audio","to":"graph.Audio"}        (stereo: graph.Left / graph.Right)
          {"op":"set","node":"osc","inputs":{"Frequency":440}}  {"op":"set_input_default","name":"RPM","value":900}
          {"op":"disconnect","to":"osc.Frequency"}  {"op":"remove_node","node":"osc"}
          {"op":"add_interface","name":"UE.Attenuation"}
        Pins match by name, case- and space-insensitively; types must match exactly (Float != Audio).
        A failed create writes nothing; edits to an existing asset apply op by op. Returns ids (alias -> node
        id) and the resulting graph (as metasound_export). audition=true plays it in the editor."""
        return _finish(
            call(
                get_conn,
                "metasound_apply",
                {
                    "asset": asset,
                    "operations": operations,
                    "format": format,
                    "one_shot": one_shot,
                    "save": save,
                    "audition": audition,
                },
                timeout=_LARGE,
            )
        )

    @mcp.tool()
    def metasound_export(asset: str) -> dict[str, Any]:
        """Read a MetaSound: format, interfaces, graph inputs/outputs with defaults, nodes (id, class, kind,
        inputs/outputs as "Name:Type", input defaults) and edges ("<node id>.<pin>")."""
        return call(get_conn, "metasound_export", {"asset": asset})

    @mcp.tool()
    def metasound_nodes(query: str = "", limit: int = 60) -> dict[str, Any]:
        """Search the registered MetaSound node classes (all words must match the class name or an input name).
        Returns class names ("UE.Biquad Filter.Audio") with their inputs/outputs as "Name:Type"."""
        return call(get_conn, "metasound_nodes", {"query": query, "limit": limit})

    @mcp.tool()
    def sound_cue_apply(
        asset: str,
        waves: list[str],
        mode: str = "",
        pitch_range: list[float] | None = None,
        volume_range: list[float] | None = None,
        loop: bool = False,
        no_repeats: bool = True,
        save: bool = True,
    ) -> dict[str, Any]:
        """Build a SoundCue from SoundWaves: one wave, or several under a Random node (mode="random", default
        for 2+ waves, no_repeats avoids playing the same one twice in a row) or a Mixer (mode="mix"), then an
        optional Modulator (pitch_range [0.9, 1.1], volume_range [0.8, 1.0]) and Looping node.
        Replaces the cue's graph. For procedural sound use metasound_apply."""
        return _finish(
            call(
                get_conn,
                "sound_cue_apply",
                {
                    "asset": asset,
                    "waves": waves,
                    "mode": mode,
                    "pitch_range": pitch_range or [],
                    "volume_range": volume_range or [],
                    "loop": loop,
                    "no_repeats": no_repeats,
                    "save": save,
                },
            )
        )

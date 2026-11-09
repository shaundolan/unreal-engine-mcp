"""UMG: build widget trees (HUDs, menus) in one call. Widget logic goes through bp_apply."""

from __future__ import annotations

from typing import Any

from . import call
from .blueprints import _finish


def register(mcp, get_conn):
    @mcp.tool()
    def widget_export(widget: str) -> dict[str, Any]:
        """Read a Widget Blueprint's tree: each widget's name, class, is_variable, text/percent,
        slot (canvas anchors/position/size/alignment, or box padding/alignment) and children."""
        return call(get_conn, "widget_export", {"widget": widget})

    @mcp.tool()
    def widget_apply(widget: str, operations: list[dict[str, Any]], create: bool = False,
                     compile: bool = True, atomic: bool = True, save: bool = True) -> dict[str, Any]:
        """Create (create=true) or edit a Widget Blueprint's tree in one undoable step.
          {"op":"add","name":"Root","class":"CanvasPanel"}                    (first add = root)
          {"op":"add","name":"HealthBar","class":"ProgressBar","parent":"Root","is_variable":true,
           "percent":1.0,"fill_color":[0.1,0.9,0.2,1],
           "slot":{"anchors":"top_left","position":[40,40],"size":[400,28],"alignment":[0,0]}}
          {"op":"add","name":"ScoreText","class":"TextBlock","text":"Score: 0","font_size":32,
           "color":[1,1,1,1],"justification":"right","is_variable":true,
           "slot":{"anchors":"top_right","position":[-40,40],"alignment":[1,0],"auto_size":true}}
          {"op":"add","name":"Menu","class":"VerticalBox","parent":"Root","slot":{"anchors":"center",...}}
          {"op":"add","name":"Title","class":"TextBlock","parent":"Menu","text":"Paused",
           "slot":{"Padding":[0,0,0,20],"HorizontalAlignment":"HAlign_Center"}}
          {"op":"set","name":"ScoreText","text":"Score: 10"}   {"op":"remove","name":"Menu"}
        anchors presets: top_left top_center top_right center_left center center_right bottom_left
        bottom_center bottom_right fill top_fill bottom_fill left_fill right_fill, or [minX,minY,maxX,maxY].
        "properties" sets any other widget property by reflection; non-canvas slot keys are slot
        properties (Padding, HorizontalAlignment, VerticalAlignment, Size).
        is_variable widgets become Blueprint variables (get_var in bp_apply, e.g. to SetPercent).
        Show it in game with bp_apply on a pawn/controller: K2Node_CreateWidget -> AddToViewport."""
        return _finish(call(get_conn, "widget_apply", {
            "widget": widget, "operations": operations, "create": create, "compile": compile,
            "atomic": atomic, "save": save}, timeout=300))

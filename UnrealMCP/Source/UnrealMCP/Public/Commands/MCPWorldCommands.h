#pragma once

#include "CoreMinimal.h"
#include "MCPCommon.h"

/**
 * World building: the landscape operations the Python API cannot reach.
 *   landscape_create   new landscape (components, sections, scale) with optional heights, material and paint layers
 *   landscape_inspect  extent, resolution, layers, material, height stats; optionally raw heights / weights
 *   landscape_write    replace heights and/or paint-layer weights, set the material, add paint layers
 * Heights travel as base64 little-endian uint16 (32768 = local Z 0, 128 units per local cm),
 * weights as base64 uint8 (0..255), both row-major over the landscape's vertex grid.
 * Procedural generation, heightmap decoding and slope/height auto-weighting run in the Python server.
 */
class UNREALMCP_API FMCPWorldCommands
{
public:
	static void Register(IMCPCommandRegistry& Registry);
};

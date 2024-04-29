# Unreal Tools

A collection of my Unreal Engine tools.

## VAT Import Setting Script - SAA

Run the VAT settings script and select the textures. This will automatically set the correct import settings for you on your mesh and VAT.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/Blender%20Vertex%20Animation%20Texture%20Pipeline.png)

If you want to set them manually you can see in the official [Unreal Docs](https://docs.unrealengine.com/5.2/en-US/vertex-animation-tool---timeline-meshes-in-unreal-engine/) what settings to use.

I've included a simple VAT material you can use to view your animation.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/vanim.gif)

## Light Ray Tool - Geometry Script

A dynamic mesh tool made in geometry script.
It follows a selected directional lights rotation. Uses distance, fresnel (and distance fields on PC) for fading. This prevents clipping and looking two dimensional.

The original intention was to be able to have rays on low end VR devices, such as the quest 2, but it also complements the volumetric rays on PC VR and flat screen PC.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/ray1.gif)
![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/ray2.gif)

Place the ray actor in the level, you can change the width, depth and rotation on the actor parameters. The rotation and scale transforms are locked.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/ray23.gif)

When happy with the look you need to bake the meshes to static. Press the Bake Mesh button on the Ray Widget.


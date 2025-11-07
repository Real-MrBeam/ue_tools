# Unreal Tools
A small suite of content-pipeline helpers for Unreal Engine that smooth over repetitive setup, expose useful debug views, and make VAT-style workflows easier.

## Luminance Reflectance Debug View - Python EUW
### UE 5.5

A custom Debug View that highlights physically implausible luminance/reflectance values, so you can spot assets that break energy conservation or reference values. Helpful when you need consistent color accuracy across a project.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/debugLum1.gif)
![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/debugLum2.gif)

Install & run

1. Unzip the tool into your project’s game directory.
2. Set the included Python script as a startup script.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/debugLum.png)

3. Restart the editor.
4. Launch the tool from the native Tools menu; close it from the same menu.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/debugLum1.png)

## Light Ray Tool - Geometry Script / EUW
### UE 4.7
**This tool requires Unreal [Geometry Script](https://dev.epicgames.com/documentation/en-us/unreal-engine/geometry-scripting-users-guide-in-unreal-engine) plugin to be enabled in your project.**

A dynamic-mesh ray-shaft generator driven by a directional light. It uses distance, Fresnel, and (on PC) Distance Fields for believable fading so the result avoids obvious planes and clipping. Originally built for low-end VR (Quest 2), but it also complements volumetric fog on PC.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/ray1.gif)

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/ray2.gif)

Usage
1. Give your sun light (directional or spotlight) an Actor Tag, by default the tool looks for "sun".
2. Place BP_LightRay in the level. Adjust Width, Depth, and Rotation via exposed params
(actor rotation/scale transforms are intentionally locked, use the params instead).
3. Use the EUW_LightRay panel:
    - Bake - Generates static meshes and hides the dynamic actor.
    - Update - Deletes the baked meshes and unhides the dynamic actor.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/Light%20Shafts-4.png)

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/euw.png)

## VAT Import Setting Script - SAA
### UE 4.7
Scripted importer settings for Vertex Animation Textures so you don’t have to configure every texture/mesh by hand.

Pair this with assets exported via my [Blender VAT Tools](https://codeberg.org/MrBeam/b3d_tools.git).
Run the VAT settings script on selected textures and mesh. This will automatically set the correct import settings for you.

- Applies consistent Unreal import settings, known to work with standard VAT materials.
- Includes a simple VAT material to validate playback quickly.

If you prefer manual setup, the official docs outline the expected settings for VAT:
[Unreal Docs](https://docs.unrealengine.com/5.2/en-US/vertex-animation-tool---timeline-meshes-in-unreal-engine/)

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/Blender%20Vertex%20Animation%20Texture%20Pipeline.png)


![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/vanim.gif)

## Custom Primitive Data Widget - EUW
### UE 4.7
An Editor Utility Widget to batch-set Custom Primitive Data across many actors/components, great for driving per-object material params like tint, dirt amount, etc.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/CPDRandom.gif)
![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/CPDOffset.gif)

Usage
1. Select target actors or mesh components.
2. Enter the parameter name (must match your material’s CPD index/name).
3. Set a value or range - Apply (or Randomize).

## Volumetric Lightmap Sampler - EUW
### UE 4.7
Samples the Volumetric Lightmap (VLM) at each selected actor’s position and writes the result into Custom Primitive Data per Static Mesh Component. Perfect for tricks like fake reflections on fully rough materials (e.g., scale reflection strength by local light level) on low-end VR.
You can use this simple [material](https://blueprintue.com/blueprint/24t7k-e6/) to try it out.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/vlmSampler.gif)

Usage
1. Run the VLM Sampler EUW.
2. The sampled value is written to a CPD parameter on each component.
3. In your material, read the CPD to modulate reflection strength.

**Ensure your level has valid VLM data to sample.**

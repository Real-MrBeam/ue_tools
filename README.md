# Unreal Tools
A small suite of content-pipeline helpers for Unreal Engine that smooth over repetitive setup, expose useful debug views, and make VAT-style workflows and low-end VR compromises easier to ship.

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

## Light Ray Tool - Geometry Script / EUW
### UE 4.7
**This tool requires Unreal [Geometry Script](https://dev.epicgames.com/documentation/en-us/unreal-engine/geometry-scripting-users-guide-in-unreal-engine) plugin to be enabled in your project.**

A dynamic-mesh ray-shaft generator driven by a directional light. It uses distance, Fresnel, and (on PC) Distance Fields for believable fading so the result avoids obvious planes and clipping. Originally built for low-end VR (Quest 2), but it also complements volumetric fog on PC.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/ray1.gif)

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/ray2.gif)

1. Give your sun light (directional or spotlight) an Actor Tag, by default the tool looks for "sun".
2. Place BP_LightRay in the level. Adjust Width, Depth, and Rotation via exposed params
(actor rotation/scale transforms are intentionally locked, use the params instead).
3. Use the EUW_LightRay panel:
    - Bake → Generates static meshes and hides the dynamic actor.
    - Update → Deletes the baked meshes and unhides the dynamic actor.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/Light%20Shafts-4.png)

- Tweak you values to your liking and bake you light rays by pressing the "bake" button on the EUW_LightRay widgets button. This will create static mesh assets for you and hide the dynamic actor in the scene. The "update" button will delete your static mesh actors and turn on visibility on the dynamic lightrays.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/euw.png)

## Custom Primitive Data Widget - EUW
### UE 4.7
An Editor Utility Widget to batch-set Custom Primitive Data across many actors/components, great for driving per-object material params like tint, dirt amount, wind phase, etc.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/CPDRandom.gif)
![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/CPDOffset.gif)

## Volumetric Lightmap Sampler - EUW
### UE 4.7
Samples the volumetric light value at every specified scene actors position, then writes the sampled volumetric light value as a CPD value on each static mesh component, enabling you to use that value as a material parameter. This is particularly handy when using [my fake reflections](https://blueprintue.com/blueprint/24t7k-e6/) for fully rough materials when developing for low-end VR devices such as the Quest. You can use the value as a reflection strength parameter to prevent reflections from glowing in the dark.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/vlmSampler.gif)

## Luminance Reflectance Debug View - Python EUW
### UE 5.5
Custom Debug View that makes it easy to spot physically incorrect luminance values. Useful for  ensuring consistent color accuracy.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/debugLum1.gif)
![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/debugLum2.gif)

- Unzip to the game dir and make sure the python script is set as a startup script.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/debugLum.png)

- Restart the editor and launch the tool via the native tools menu. You close it in the same manner.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/debugLum1.png)
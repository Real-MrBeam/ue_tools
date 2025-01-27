# Unreal Tools
A sample of my Unreal Engine tools.

## VAT Import Setting Script - SAA
Made for making vertex animation textures importing easier. For example animations made by using my [Blender VAT Tools](https://codeberg.org/MrBeam/b3d_tools.git).
Run the VAT settings script and select the textures and mesh. This will automatically set the correct import settings for you.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/Blender%20Vertex%20Animation%20Texture%20Pipeline.png)

If you want to set them manually you can see in the official [Unreal Docs](https://docs.unrealengine.com/5.2/en-US/vertex-animation-tool---timeline-meshes-in-unreal-engine/) what settings to use.
I've included a simple VAT material you can use to view your animation.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/vanim.gif)

## Light Ray Tool - Geometry Script / EUW
### Dependencies
**This tool requires Unreal [Geometry Script](https://dev.epicgames.com/documentation/en-us/unreal-engine/geometry-scripting-users-guide-in-unreal-engine) plugin to be enabled in your project.**

A dynamic mesh tool made in geometry script.
It follows a selected directional lights rotation. Uses distance, fresnel (and distance fields on PC) for fading. This prevents clipping and looking two dimensional.
The original intention was to be able to have rays on low end VR devices, such as the quest 2, but it also complements the volumetric rays on PC VR and flat screen PC.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/ray1.gif)
![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/ray2.gif)

- Make sure that your directional or spotlight you use as sun has an actor tag with a name that makes sense. As default the tool is looking for is "sun".
- Place the BP_LightRay actor in the scene, you can change the width, depth and rotation on the actor parameters. The rotation and scale transforms are locked.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/Light%20Shafts-4.png)

- Tweak you values to your liking and bake you light rays by pressing the "bake" button on the EUW_LightRay widgets button. This will create static mesh assets for you and hide the dynamic actor in the scene. The "update" button will delete your static mesh actors and turn on visibility on the dynamic lightrays.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/euw.png)

## Custom Primitive Data Widget - EUW
With this tool you can set scalar and color CPD values easily, on multiple objects at once. You can even set random values if you like.
Write the parameter name you want to change in the parameter field when using it.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/CPDRandom.gif)
![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/CPDOffset.gif)

## Volumetric Lightmap Sampler - EUW
Samples the volumetric light value at every filtered scene actor, then writes the sampled volumetric light value as a CPD value on each static mesh component, enabling you to use that value as a material parameter. This is particularly handy when using [my fake reflections](https://blueprintue.com/blueprint/24t7k-e6/) for fully rough materials when developing for low-end VR devices such as the Quest. You can use the value as a reflection strength parameter to prevent reflections from glowing in the dark.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/vlmSampler.gif)

## Luminance Reflectance Debug View - Python EUW
Custom Debug View that makes it easy to spot incorrect luminance values. Useful for  ensuring consistent color accuracy.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/debugLum1.gif)
![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/debugLum2.gif)

# Miscellaneous Tools
Smaller scripts.

## Static Mesh Build Settings - SSA
Bulk edit mesh build settings.

![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/UnrealEditor_7KtwBIaWCq.png)
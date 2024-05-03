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
### Dependencies
**This tool relies on an external Unreal Engine plugin to function properly. Please ensure you have the [UMG Color Pickers](https://www.unrealengine.com/marketplace/en-US/product/umg-color-pickers-01) plugin installed for this tool to work.**

With this tool you can set scalar and color CPD values easily, on multiple objects at once. You can even set random values if you like.
Write the parameter name you want to change in the parameter field when using it.

![image](https://codeberg.org/MrBeam/ue_tools/raw/commit/9a5e63967f3ad99f30ef8b3939239e4bccab1ab3/readme/CPDRandom.gif)
![image](https://codeberg.org/MrBeam/ue_tools/raw/branch/main/readme/CPDOffset.gif)
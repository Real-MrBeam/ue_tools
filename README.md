# Unreal Tools
A small suite of content-pipeline helpers for Unreal Engine that smooth over repetitive setup, expose useful debug views, and make VAT-style workflows easier.

## Luminance Reflectance Debug View - Python EUW
### UE 5.7
A custom debug view that highlights physically implausible luminance and reflectance values, so you can spot assets that break energy conservation or stray from reference values.

![image](readme/debugLum1.gif)

[Install and usage](DebugViewAlbedoRange/README.md)

## Memreport Viewer - Python
### UE 5
A desktop viewer for `memreport -full` captures. It charts resident texture memory for every capture in a folder and shows which textures were added, removed, grew or shrank between captures.

![image](readme/poolview.gif)

[Install and usage](memreportViewer/README.md)

## Light Ray Tool - Geometry Script / EUW
### UE 5
A dynamic-mesh ray-shaft generator driven by a directional light. It fades with distance, Fresnel and Distance Fields, so the result avoids obvious planes and clipping. Built for low-end VR, but it also complements volumetric fog on PC.

![image](readme/ray1.gif)

![image](readme/ray2.gif)

[Install and usage](lightrayTools/README.md)

## VAT Import Setting Script - SAA
### UE 5
A scripted asset action that applies the import settings Vertex Animation Textures need, so you don't have to configure every texture and mesh by hand. Pairs with my [Blender VAT Tools](https://github.com/Real-MrBeam/b3d_tools).

![image](readme/Blender%20Vertex%20Animation%20Texture%20Pipeline.png)

[Install and usage](vatTools/README.md)

## Custom Primitive Data Widget - EUW
### UE 5
An Editor Utility Widget that batch-sets Custom Primitive Data across many actors and components. Good for driving per-object material parameters such as tint or dirt amount.

![image](readme/CPDRandom.gif)

[Install and usage](cpdWidget/README.md)

## Volumetric Lightmap Sampler - EUW
### UE 5
An Editor Utility Widget that samples the Volumetric Lightmap at each selected actor's position and writes the result into Custom Primitive Data, for tricks like fake reflections on fully rough materials.

![image](readme/vlmSampler.gif)

[Install and usage](vlmSampler/README.md)

import unreal

def open_editor_utility_widget_with_forced_size():
    """
    Opens an Editor Utility Widget with a forced size, bypassing the saved layout settings.
    """
    # Define the path to your Editor Utility Widget Blueprint
    widget_path = "/Game/Blueprints/EditorUtilities/EUW_AlbedoRangeSettings"

    # Load the Editor Utility Widget Blueprint
    widget_asset = unreal.EditorAssetLibrary.load_asset(widget_path)
    if not widget_asset:
        unreal.log_error(f"Failed to load Editor Utility Widget: {widget_path}")
        return

    # Ensure the loaded asset is an Editor Utility Widget Blueprint
    if not widget_asset.get_class().get_name() == "EditorUtilityWidgetBlueprint":
        unreal.log_error(f"The asset at {widget_path} is not an Editor Utility Widget Blueprint.")
        return

    # Get the Editor Utility Subsystem
    subsystem = unreal.get_editor_subsystem(unreal.EditorUtilitySubsystem)

    widget_width = 100
    widget_height = 200

    subsystem.spawn_and_register_tab_with_size(widget_asset, widget_width, widget_height)

    unreal.log(f"Opened Editor Utility Widget with forced size: {widget_width}x{widget_height}.")

open_editor_utility_widget_with_forced_size()

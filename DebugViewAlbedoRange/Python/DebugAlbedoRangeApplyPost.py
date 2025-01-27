import unreal

def find_actor_by_name(actor_name_prefix):
    """
    Finds an actor in the current level whose name starts with the given prefix.
    :param actor_name_prefix: The prefix of the actor's name to search for.
    :return: The actor object if found, None otherwise.
    """
    # Access the Editor Actor Subsystem
    editor_actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    
    # Get all actors in the level
    all_actors = editor_actor_subsystem.get_all_level_actors()

    # Search for the actor by name prefix
    for actor in all_actors:
        if actor.get_name().startswith(actor_name_prefix):
            return actor

    return None


def delete_actor(actor):
    """
    Deletes the given actor from the level.
    """
    if actor:
        # Delete the actor
        editor_actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        editor_actor_subsystem.destroy_actor(actor)
        unreal.log(f"Deleted existing actor: {actor.get_name()}")


def run_editor_utility_widget():
    """
    Runs an Editor Utility Widget Blueprint by loading it and spawning it as a tab.
    """
    widget_path = "/Game/Blueprints/EditorUtilities/DebugViewAlbedoRange/EUW_AlbedoRangeSettings"

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

    # Spawn and register the widget as a new tab
    subsystem.spawn_and_register_tab(widget_asset)

    unreal.log(f"Successfully executed Editor Utility Widget Blueprint: {widget_path}")


def spawn_bp_actor():
    """
    Spawns a Blueprint actor into the current level if it does not already exist.
    If the actor exists, it will be deleted instead of spawning a new one.
    """
    blueprint_path = "/Game/Blueprints/EditorUtilities/DebugViewAlbedoRange/BP_DebugViewAlbedoPPV"

    # Load the Blueprint asset
    blueprint_asset = unreal.EditorAssetLibrary.load_asset(blueprint_path)
    if not blueprint_asset:
        unreal.log_error(f"Failed to load Blueprint: {blueprint_path}")
        return

    # Ensure the loaded asset is a Blueprint
    if not isinstance(blueprint_asset, unreal.Blueprint):
        unreal.log_error(f"The asset at {blueprint_path} is not a Blueprint.")
        return

    # Get the generated class from the Blueprint asset
    blueprint_class = blueprint_asset.generated_class()
    if not blueprint_class:
        unreal.log_error(f"Failed to get the BlueprintGeneratedClass from the asset: {blueprint_path}")
        return

    # Actor name prefix to check for 
    actor_name_prefix = "BP_DebugViewAlbedoPPV"

    # Check if the actor already exists
    existing_actor = find_actor_by_name(actor_name_prefix)

    if existing_actor:
        # Actor exists, delete it
        delete_actor(existing_actor)
        unreal.log(f"Actor {actor_name_prefix} exists and was deleted. EUW will NOT run.")
    else:
        # Actor doesn't exist, spawn a new one
        spawn_location = unreal.Vector(0.0, 0.0, 0.0)
        spawn_rotation = unreal.Rotator(0.0, 0.0, 0.0)

        try:
            spawned_actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
                actor_class=blueprint_class,  # Pass the correct class
                location=spawn_location,
                rotation=spawn_rotation
            )

            if spawned_actor:
                unreal.log(f"Successfully spawned actor {spawned_actor.get_name()} at {spawn_location}.")
                run_editor_utility_widget()
            else:
                unreal.log_error("Failed to spawn the actor.")
        except Exception as e:
            unreal.log_error(f"Error during actor spawn: {str(e)}")


spawn_bp_actor()

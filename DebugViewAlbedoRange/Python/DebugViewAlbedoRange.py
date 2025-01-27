import unreal

# Debug print function
def debug_log(message):
    unreal.log_warning(f"[Debug] {message}")

def register_debug_view_tool():
    """
    Registers a menu entry in the Tools menu to run a Python script from the project's root.
    """
    debug_log("register_debug_view_tool() called.")

    # Get or create the Tools menu
    tool_menus = unreal.ToolMenus.get()
    menu = tool_menus.find_menu("LevelEditor.MainMenu.Tools")
    if not menu:
        menu = tool_menus.add_menu("LevelEditor.MainMenu.Tools", "Tools")

    # Add a section for Debugging tools
    menu.add_section("Debugging", "Debugging Tools")

    # Add an entry to execute the external Python script
    entry = unreal.ToolMenuEntry(
        name="DebugViewAlbedoRange",
        type=unreal.MultiBlockType.MENU_ENTRY,
        insert_position=unreal.ToolMenuInsert("Debugging", unreal.ToolMenuInsertType.FIRST)
    )

    # Keep the original label and tooltip
    entry.set_label("Debug View: Albedo Range")
    entry.set_tool_tip("Toggle debug view for albedo range")

    # Use a project-relative path
    project_relative_path = unreal.Paths.project_content_dir() + "Python/DebugAlbedoRangeApplyPost.py"
    entry.set_string_command(
        type=unreal.ToolMenuStringCommandType.PYTHON,
        custom_type="",
        string=f'exec(open("{project_relative_path}").read())'  # Execute script using project-relative path
    )

    # Add the entry to the menu and refresh
    menu.add_menu_entry("Debugging", entry)
    tool_menus.refresh_all_widgets()
    debug_log(f"Menu registered successfully with script path: {project_relative_path}")

# Register the tool
debug_log("Registering debug view tool...")
register_debug_view_tool()

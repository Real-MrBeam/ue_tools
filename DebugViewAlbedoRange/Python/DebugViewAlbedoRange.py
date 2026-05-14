import unreal

def debug_log(message):
	unreal.log_warning(f"[Debug] {message}")


def open_albedo_range_widget():
	"""
	Opens the Albedo Range Settings Editor Utility Widget.
	"""
	widget_path = "/Game/BufferVisualization/EUW_AlbedoRangeSettings"

	widget_asset = unreal.EditorAssetLibrary.load_asset(widget_path)
	if not widget_asset:
		unreal.log_error(f"Failed to load Editor Utility Widget: {widget_path}")
		return

	if not widget_asset.get_class().get_name() == "EditorUtilityWidgetBlueprint":
		unreal.log_error(f"The asset at {widget_path} is not an Editor Utility Widget Blueprint.")
		return

	subsystem = unreal.get_editor_subsystem(unreal.EditorUtilitySubsystem)
	subsystem.spawn_and_register_tab(widget_asset)

	unreal.log(f"Opened Editor Utility Widget: {widget_path}")


def register_debug_view_tool():
	"""
	Registers a menu entry in the Tools menu.
	"""
	debug_log("register_debug_view_tool() called.")

	tool_menus = unreal.ToolMenus.get()
	menu = tool_menus.find_menu("LevelEditor.MainMenu.Tools")
	if not menu:
		menu = tool_menus.add_menu("LevelEditor.MainMenu.Tools", "Tools")

	menu.add_section("Debugging", "Debugging Tools")

	entry = unreal.ToolMenuEntry(
		name="DebugViewAlbedoRange",
		type=unreal.MultiBlockType.MENU_ENTRY,
		insert_position=unreal.ToolMenuInsert("Debugging", unreal.ToolMenuInsertType.FIRST)
	)
	entry.set_label("Debug View: Albedo Range")
	entry.set_tool_tip("Open the Albedo Range debug view settings widget")

	project_relative_path = unreal.Paths.project_content_dir() + "Python/DebugViewAlbedoRange.py"
	entry.set_string_command(
		type=unreal.ToolMenuStringCommandType.PYTHON,
		custom_type="",
		string=f'exec(open(r"{project_relative_path}").read()); open_albedo_range_widget()'
	)

	menu.add_menu_entry("Debugging", entry)
	tool_menus.refresh_all_widgets()
	debug_log(f"Menu registered with script path: {project_relative_path}")


debug_log("Registering debug view tool...")
register_debug_view_tool()
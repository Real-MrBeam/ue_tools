import unreal


MENU_NAME = "LevelEditor.MainMenu.Tools"
SECTION_NAME = "OLTools"
SUBMENU_NAME = "UDIMTools"
UDIM_ENTRY_NAMES = (
    "OpenUDIMBuilder",
    "BuildAndImportUDIMs",
    "ImportLatestUDIMs",
    "ImportLatestSelectedUDIMs",
    "AuditUDIMResolutions",
    "LaunchEUW_UDIM",
)


def _pipeline_command(function_name):
    return (
        "import importlib\n"
        "import UDIMPipeline\n"
        "importlib.reload(UDIMPipeline)\n"
        f"UDIMPipeline.{function_name}()\n"
    )


def _add_python_entry(menu, name, label, tooltip, command, insert_name="", insert_type=None):
    if insert_type is None:
        insert_type = unreal.ToolMenuInsertType.DEFAULT
    entry = unreal.ToolMenuEntry(
        name=name,
        type=unreal.MultiBlockType.MENU_ENTRY,
        insert_position=unreal.ToolMenuInsert(insert_name, insert_type),
    )
    entry.set_label(label)
    entry.set_tool_tip(tooltip)
    entry.set_string_command(
        type=unreal.ToolMenuStringCommandType.PYTHON,
        custom_type=unreal.Name(""),
        string=command,
    )
    menu.add_menu_entry("", entry)


def _add_console_entry(menu, name, label, tooltip, command, insert_name="", insert_type=None):
    if insert_type is None:
        insert_type = unreal.ToolMenuInsertType.DEFAULT
    entry = unreal.ToolMenuEntry(
        name=name,
        type=unreal.MultiBlockType.MENU_ENTRY,
        insert_position=unreal.ToolMenuInsert(insert_name, insert_type),
    )
    entry.set_label(label)
    entry.set_tool_tip(tooltip)
    entry.set_string_command(
        type=unreal.ToolMenuStringCommandType.COMMAND,
        custom_type=unreal.Name(""),
        string=command,
    )
    menu.add_menu_entry("", entry)


def register_udim_tool():
    tool_menus = unreal.ToolMenus.get()
    menu = tool_menus.extend_menu(MENU_NAME)
    menu.add_section(SECTION_NAME, label=unreal.Text("OL Tools"))
    for entry_name in UDIM_ENTRY_NAMES:
        tool_menus.remove_entry(MENU_NAME, SECTION_NAME, entry_name)

    submenu = menu.add_sub_menu(
        owner=menu.menu_name,
        section_name=SECTION_NAME,
        name=SUBMENU_NAME,
        label="UDIM Tools",
        tool_tip="Build and import UDIM textures.",
    )
    tool_menus.remove_entry(submenu.menu_name, "", "LaunchEUW_UDIM")

    _add_python_entry(
        submenu,
        "OpenUDIMBuilder",
        "Open UDIM Builder",
        "Preview, validate and generate UDIM source tiles.",
        _pipeline_command("open_builder"),
        insert_type=unreal.ToolMenuInsertType.FIRST,
    )
    _add_python_entry(
        submenu,
        "BuildAndImportUDIMs",
        "Build and Import UDIMs",
        "Sync and validate sources, generate complete UDIM sets, then import successful sets.",
        _pipeline_command("build_and_import"),
        insert_name="OpenUDIMBuilder",
        insert_type=unreal.ToolMenuInsertType.AFTER,
    )
    _add_python_entry(
        submenu,
        "ImportLatestUDIMs",
        "Import Latest UDIM Build",
        "Import the most recent successful UDIM manifest without rebuilding source tiles.",
        _pipeline_command("import_latest_manifest"),
        insert_name="BuildAndImportUDIMs",
        insert_type=unreal.ToolMenuInsertType.AFTER,
    )
    _add_python_entry(
        submenu,
        "ImportLatestSelectedUDIMs",
        "Import Latest UDIM Build (Selected Assets)",
        "Import only currently selected Content Browser assets that are present in the latest manifest.",
        _pipeline_command("import_latest_selected"),
        insert_name="ImportLatestUDIMs",
        insert_type=unreal.ToolMenuInsertType.AFTER,
    )
    tool_menus.refresh_all_widgets()


register_udim_tool()

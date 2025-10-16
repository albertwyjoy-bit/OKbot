from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = collect_submodules("kimi_cli.tools")
datas = (
    collect_data_files(
        "kimi_cli",
        includes=["**/*.yaml", "**/*.md"],
    )
    + collect_data_files(
        "dateparser",
        includes=["**/*.pkl"],
    )
    + collect_data_files(
        "fastmcp",
        includes=["../fastmcp-*.dist-info/*"],
    )
)

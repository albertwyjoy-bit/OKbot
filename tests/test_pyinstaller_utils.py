from inline_snapshot import snapshot


def test_pyinstaller_datas():
    from kimi_cli.utils.pyinstaller import datas

    assert sorted(datas) == snapshot(
        [
            (
                "/Users/moonshot/OpenProjects/ensoul/.venv/lib/python3.13/site-packages/dateparser/data/dateparser_tz_cache.pkl",
                "dateparser/data",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/CHANGELOG.md",
                "kimi_cli",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/agents/koder/README.md",
                "kimi_cli/agents/koder",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/agents/koder/agent.yaml",
                "kimi_cli/agents/koder",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/agents/koder/sub.yaml",
                "kimi_cli/agents/koder",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/agents/koder/system.md",
                "kimi_cli/agents/koder",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/tools/bash/bash.md",
                "kimi_cli/tools/bash",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/tools/dmail/dmail.md",
                "kimi_cli/tools/dmail",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/tools/file/glob.md",
                "kimi_cli/tools/file",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/tools/file/grep.md",
                "kimi_cli/tools/file",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/tools/file/patch.md",
                "kimi_cli/tools/file",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/tools/file/read.md",
                "kimi_cli/tools/file",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/tools/file/replace.md",
                "kimi_cli/tools/file",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/tools/file/write.md",
                "kimi_cli/tools/file",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/tools/task/task.md",
                "kimi_cli/tools/task",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/tools/think/think.md",
                "kimi_cli/tools/think",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/tools/todo/set_todo_list.md",
                "kimi_cli/tools/todo",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/tools/web/fetch.md",
                "kimi_cli/tools/web",
            ),
            (
                "/Users/moonshot/OpenProjects/ensoul/src/kimi_cli/tools/web/search.md",
                "kimi_cli/tools/web",
            ),
        ]
    )


def test_pyinstaller_hiddenimports():
    from kimi_cli.utils.pyinstaller import hiddenimports

    assert sorted(hiddenimports) == snapshot(
        [
            "kimi_cli.tools",
            "kimi_cli.tools.bash",
            "kimi_cli.tools.dmail",
            "kimi_cli.tools.file",
            "kimi_cli.tools.file.glob",
            "kimi_cli.tools.file.grep",
            "kimi_cli.tools.file.patch",
            "kimi_cli.tools.file.read",
            "kimi_cli.tools.file.replace",
            "kimi_cli.tools.file.write",
            "kimi_cli.tools.result_builder",
            "kimi_cli.tools.task",
            "kimi_cli.tools.test",
            "kimi_cli.tools.think",
            "kimi_cli.tools.todo",
            "kimi_cli.tools.utils",
            "kimi_cli.tools.web",
            "kimi_cli.tools.web.fetch",
            "kimi_cli.tools.web.search",
        ]
    )

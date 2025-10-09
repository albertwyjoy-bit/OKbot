# Changelog

<!--
Release notes will be parsed and available as /release-notes
The parser extracts for each version:
  - a short description (first paragraph after the version header)
  - bullet entries beginning with "- " under that version (across any subsections)
Internal builds may append content to the Unreleased section.
Only write entries that are worth mentioning to users.
-->

## [0.22] - 2025-10-09

## Changed

- Improve `SearchWeb` and `FetchURL` tool call visualization
- Improve search result output format

## [0.21] - 2025-10-09

### Added

- Add `--print` option as a shortcut for `--ui print`, `--acp` option as a shortcut for `--ui acp`
- Support `--output-format stream-json` to print output in JSON format
- Add `SearchWeb` tool with `services.moonshot_search` configuration. You need to configure it with `"services": {"moonshot_search": {"api_key": "your-search-api-key"}}` in your config file.
- Add `FetchURL` tool
- Add `Think` tool
- Add `PatchFile` tool, not enabled in Kimi Koder agent
- Enable `SendDMail` and `Task` tool in Kimi Koder agent with better tool prompts
- Add `ENSOUL_NOW` builtin system prompt argument

### Changed

- Better-looking `/release-notes`
- Improve tool descriptions
- Improve tool output truncation

## [0.20] - 2025-09-30

### Added

- Add `--ui acp` option to start Agent Client Protocol (ACP) server

## [0.19] - 2025-09-29

### Added

- Support piped stdin for print UI
- Support `--input-format=stream-json` for piped JSON input

### Fixed

- Do not include `CHECKPOINT` messages in the context when `SendDMail` is not enabled

## [0.18] - 2025-09-29

### Added

- Support `max_context_size` in LLM model configurations to configure the maximum context size (in tokens)

### Improved

- Improve `ReadFile` tool description

## [0.17] - 2025-09-29

### Fixed

- Fix step count in error message when exceeded max steps
- Fix history file assertion error in `kimi_run`
- Fix error handling in print mode and single command shell mode
- Add retry for LLM API connection errors and timeout errors

### Changed

- Increase default max-steps-per-run to 100

## [0.16.0] - 2025-09-26

### Tools

- Add `SendDMail` tool (disabled in Kimi Koder, can be enabled in custom agent)

### SDK

- Session history file can be specified via `_history_file` parameter when creating a new session

## [0.15.0] - 2025-09-26

- Improve tool robustness

## [0.14.0] - 2025-09-25

### Added

- Add `StrReplaceFile` tool

### Improved

- Emphasize the use of the same language as the user

## [0.13.0] - 2025-09-25

### Added

- Add `SetTodoList` tool
- Add `User-Agent` in LLM API calls

### Improved

- Better system prompt and tool description
- Better error messages for LLM

## [0.12.0] - 2025-09-24

### Added

- Add `print` UI mode, which can be used via `--ui print` option
- Add logging and `--debug` option

### Changed

- Catch EOF error for better experience

## [0.11.1] - 2025-09-22

### Changed

- Rename `max_retry_per_step` to `max_retries_per_step`

## [0.11.0] - 2025-09-22

### Added

- Add `/release-notes` command
- Add retry for LLM API errors
- Add loop control configuration, e.g. `{"loop_control": {"max_steps_per_run": 50, "max_retry_per_step": 3}}`

### Changed

- Better extreme cases handling in `read_file` tool
- Prevent Ctrl-C from exiting the CLI, force the use of Ctrl-D or `exit` instead

## [0.10.1] - 2025-09-18

- Make slash commands look slightly better
- Improve `glob` tool

## [0.10.0] - 2025-09-17

### Added

- Add `read_file` tool
- Add `write_file` tool
- Add `glob` tool
- Add `task` tool

### Changed

- Improve tool call visualization
- Improve session management
- Restore context usage when `--continue` a session

## [0.9.0] - 2025-09-15

- Remove `--session` and `--continue` options

## [0.8.1] - 2025-09-14

- Fix config model dumping

## [0.8.0] - 2025-09-14

- Add `shell` tool and basic system prompt
- Add tool call visualization
- Add context usage count
- Support interrupting the agent loop
- Support project-level `AGENTS.md`
- Support custom agent defined with YAML
- Support oneshot task via `kimi -c`

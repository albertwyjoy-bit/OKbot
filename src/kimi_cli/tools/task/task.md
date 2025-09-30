Delegates self-contained tasks to specialized subagents for context isolation and focused problem-solving.

**When to use:**
- **Context isolation**: Offload lengthy debugging, compilation fixes, or multi-step problem-solving that would pollute your main context
- **Complex analysis**: Tasks requiring deep exploration, investigation, or iterative problem-solving, of which only the final result rather than the entire process matters to you

**When NOT to use:**
- Simple single-tool operations (use direct tools instead)
- Tasks that may require your entire context as background information
- Actions that need immediate feedback or course-correction

**Key Benefits:**
- **Context Preservation**: Subagents work in isolated contexts - their detailed problem-solving process won't clutter your main conversation
- **Focused Results**: You receive only the final solution/summary, not the entire trial-and-error process
- **Parallel Processing**: Delegate multiple independent tasks simultaneously

**Critical Guidelines:**
- **Autonomous Operation**: Once delegated, subagents work independently. No further communication until completion.
- **Detailed Prompts Required**: Include ALL context, specific steps, and exact output format. The prompt IS the contract.
- **Result Processing**: Subagent returns final report to you only. You are responsible for interpreting and presenting relevant information to user.

**Available Subagents:**
- `explorer`: Project analysis and codebase exploration
- `coder`: Multi-step coding, debugging, refactoring, and error resolution

**Example Use Cases:**
- Fix compilation errors without cluttering context with build logs
- Debug complex issues through systematic investigation
- Refactor legacy code with iterative improvements
- Analyze project structure and dependencies

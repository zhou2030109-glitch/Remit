---
name: handoff
description: Compact the current conversation into a handoff document for another agent to pick up.
argument-hint: "What will the next session be used for?"
---

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Create a unique temporary file using an available platform API, such as Python's `tempfile` or .NET's `System.IO.Path.GetTempFileName`, that creates the file without overwriting an existing one. Read the newly created file before writing to it.

`mktemp -t handoff-XXXXXX.md` is an example for platforms that support it, not a required dependency. Use an available API rather than installing tools just to reproduce that command.

Suggest the skills to be used, if any, by the next session.

Do not duplicate content already captured in other artifacts (PRDs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.

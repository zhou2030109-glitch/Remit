# AI-native UI Patterns (trust + control)

AI UI should feel like a product feature, not a bolt-on chatbot. Prioritize clarity, trust, and user control.

## Core patterns

### Copilot side panel (assistive AI)
- Persistent panel (often right rail) with:
  - header + **AI disclosure**
  - prompt input (or command chips/templates)
  - output stream area
  - actions (apply, copy, discard)

### AI-generated labeling (strict)
- AI output must not be visually confused with user-authored content.
- Use a badge/label (“AI-generated”) with tooltip text and/or subtle tinting.

### Citations / sources (recommended for factual outputs)
- Render as chips or footnotes under the relevant block.
- Make them clickable and scannable (title + optional section anchor).

### “Why this?” affordance
- Provide a short “why this suggestion?” expansion with neutral language.
- Prefer concrete reasons (policy, data, heuristic) over anthropomorphic phrasing.

## Human-in-the-loop (strict)
- Provide at least one of:
  - **Apply / Undo**
  - **Accept / Reject**
  - confirmation for destructive actions
- Never silently perform irreversible changes.

## AI loading and motion
- Use subtle, purposeful indicators:
  - typing dots, shimmer skeleton, progressive reveal
- Respect `prefers-reduced-motion` (no endless animated flourish).

## Signature move ideas (pick one)
- Citation rail (chips aligned to the right edge of the answer)
- Apply stack (queued changes with an “apply all” + per-item undo)
- Confidence capsule (uncertainty/assumptions shown as compact pills)

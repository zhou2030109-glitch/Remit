---
name: frontend-design
description: "Create distinctive, production-grade frontend UI with an intentional aesthetic direction, purposeful layouts, meaningful motion, and strong accessibility. Follow the existing project's framework and conventions; consider React, Next.js, or vanilla HTML/CSS/JS for new projects without an established stack."
---

# Frontend Design

Build memorable, functional interfaces with a clear point-of-view (no generic templates). Commit to one aesthetic direction, define tokens, ship accessible + responsive UI, and keep changes scoped.

For targeted fixes in an existing interface, preserve its design direction and tokens. The aesthetic thesis and signature-move steps below apply to new designs or requested redesigns; they do not require adding a visual feature or starting a design interview for a small fix.

## Preferred Targets (choose the best fit)
For an existing project, use its established framework, router, and conventions, including Vue or other stacks. The following preferences apply only to new projects without an established stack.

1) **Next.js (TypeScript)**: preferred for production app/marketing routes and design system work.
2) **React (TypeScript)**: preferred for SPA/component libraries outside Next.js.
3) **Vanilla HTML/CSS/JS**: great for static demos, prototypes, and standalone embeds.

## Quick Start (do this first)
1) Inspect the relevant components, routes, project configuration, and user request. Reuse established decisions about the audience, task, stack, and constraints. Ask only about material unresolved choices that cannot be determined from this context; zero questions is valid. Preserve the existing framework and router. Continue independent authorized work while a necessary answer is pending.
2) Pick an aesthetic direction + **one signature move**. If vague, propose 2–3 options from `references/aesthetic-playbook.md`.
3) Define tokens (type, palette, spacing, radius, shadows, motion). Keep accents to 1–2.
4) Build structure first (semantic markup + layout), then motion, then polish + QA.

## Guardrails (avoid footguns)
- Keep scope tight: redesign only what the user asked for.
- Reuse existing tokens/components if working in an existing repo; do not introduce a new global styling system unless requested.
- Match complexity to the vision: refined minimalism needs restraint; maximalism needs deliberate structure (not random decoration).
- Respect `prefers-reduced-motion` for all animations.
- Avoid “AI slop” defaults. If the repo already uses a generic font, keep it unless the user explicitly wants new typography.
- Enforce token semantics: error/success/warning colors are for meaning, not decoration; keep contrast accessible in light/dark.
- Default to shipping code that is easy to maintain: small components, clear hierarchy, minimal dependencies.
- Don’t add new packages (animation libs, UI kits, icon sets) unless the user explicitly asks or the repo already uses them.
- Default to one primary CTA per view; demote the rest (secondary/tertiary) to avoid competing actions.

## Workflow

### 1) Commit to a direction
- Declare: aesthetic direction, palette mood, type pairing, and the signature move.
- Write a 1–2 sentence design thesis that explains the hierarchy and vibe.
- Use `references/anti-patterns.md` to avoid predictable layouts and cliched palettes.

### 1b) Design system rules (strict vs flexible)
- **Strict**: semantic color meaning, readable type ramp, reduced motion, visible focus, and “human-in-control” for AI actions (undo/apply/confirm).
- **Flexible**: brand accent hue, surface “temperature” in dark mode, and density preset (compact vs comfy).
- **When to read more**: load `references/tokens-and-semantics.md` when defining tokens and `references/ai-native-ui-patterns.md` when adding AI surfaces.

### 1a) React/Next.js coding guidance (read when implementing in React/Next.js)
- **React fundamentals**: keep components pure; prefer event handlers over Effects for user-driven work; follow the Rules of Hooks.
- **Next.js App Router**: keep Client Components small; use Server Components by default; mark `"use client"` only where you need state, effects, or browser APIs.
- **Data fetching**: prefer server-side `fetch` and explicit cache/revalidate controls; avoid unnecessary client-side effects for data.
- **When to read more**: load `references/react-next-best-practices.md` before writing complex React logic or introducing new data fetching patterns.

### 2) Plan tokens and layout (before details)
- Define CSS variables (or a theme object) for:
  - Color: background/surface/ink/muted/accent(+optional accent2)
  - Type: display/body/mono, size scale, line-height
  - Shape: radius scale and border rules
  - Depth: shadow and outline rules
  - Motion: durations + easing + reveal stagger step
- Lay out the page/component skeleton and content hierarchy.
- Reference `references/implementation-patterns.md` for patterns and QA.

### 3) Implement (ship code, not moodboards)
- Start with semantic structure and accessible interactions (keyboard + focus states).
- Build macro layout with Grid; micro layout with Flexbox.
- For Next.js/React, keep components small and composable; prefer CSS Modules or the repo’s existing styling approach.
- Use “link for navigation, button for actions” to keep semantics and keyboard behavior correct.
- Apply the signature move sparingly but decisively (one memorable thing, not ten).
- Add motion as orchestration (one strong page-load + a few micro-interactions), not random flourishes.

### 3a) Next.js implementation notes (when applicable)
- **Router**: follow the project's existing router; prefer App Router (`app/`) only for a new Next.js project without an established routing choice.
- **Server vs Client**: default to Server Components; add `"use client"` only for interactive islands.
- **Data + state**: keep data fetching on the server when possible; keep client state local and purposeful.
- **Performance**: avoid layout shift (stable heights, predictable grids); keep blur/shadows paint-friendly on mobile.
- **Platform primitives**: prefer `next/link` for navigation and `next/image` for real images when available.
- **Fonts**: if changing typography in Next.js, prefer `next/font` to avoid FOIT/FOUT surprises.
- **Styling**: keep it consistent—CSS Modules, Tailwind, or the existing system; don’t introduce a new one without asking.

### 3b) Content + states (don’t ship empty shells)
- Define loading, empty, and error states for any data-driven UI.
- Keep copy tight and purposeful; avoid placeholder lorem unless asked.
- Preserve existing product terminology unless the user requests copy changes.

### 4) Polish and QA
- Responsive pass: collapse grids, maintain padding, preserve CTA prominence on mobile.
- Accessibility pass: landmarks, one `h1`, labels, contrast, focus order, skip link when relevant.
- Performance pass: avoid expensive shadows on mobile, keep animation paint-friendly, avoid large images.

## Definition of Done
- Layout looks intentional at mobile + desktop breakpoints (no cramped type, no orphan CTAs).
- Focus states are visible, tab order is logical, and motion respects `prefers-reduced-motion`.
- Tokens are centralized (CSS variables/theme), and the signature move is present but not overused.
- For implementation tasks, run available checks relevant to the change and distinguish observed results from unverified behavior. If the environment blocks a check, report the specific limitation and complete independent authorized work.

## Deliverables (how to present results)
- List files changed and what they do.
- Explain the aesthetic direction + signature move (1–2 sentences).
- Report the validation actually performed and its results, using the repo's package manager and check conventions. Include commands when useful for reproduction; supplying commands alone does not establish that validation passed. State any checks that remain blocked or unrun.

## Bundled Resources
- `references/aesthetic-playbook.md`: strong directions, type+color cues, signature moves.
- `references/implementation-patterns.md`: layout/motion/a11y patterns for vanilla + React.
- `references/anti-patterns.md`: quick “don’t do this” checklist to avoid generic output.
- `references/react-next-best-practices.md`: React + Next.js coding guidelines (hooks, effects, server/client, data fetching).
- `references/tokens-and-semantics.md`: token semantics, type ramp, dark mode, and density rules (strict vs flexible).
- `references/ai-native-ui-patterns.md`: copilot panels, citations, disclosures, and human-in-the-loop UI patterns.
- `assets/vanilla-starter/`: runnable vanilla starter you can copy + retheme via CSS variables.
- `assets/react-component-starter/`: small React component + CSS module skeleton to retheme.
- `assets/nextjs-app-router-starter/`: minimal Next.js App Router page + tokens you can copy and retheme.

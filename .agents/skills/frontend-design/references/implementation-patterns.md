# Implementation Patterns

Use these patterns to move quickly while keeping the aesthetic sharp. Prefer real code and real semantics over “design vibes”.

## Framework choice
Keep the existing project's framework, router, and component conventions, including Vue or other stacks. The following choices apply only when no stack is established:

- Use **vanilla** for quick concepts, single pages, marketing-style layouts.
- Consider **React** for a new app that needs state, routes, or reusable components.

## Vanilla HTML/CSS/JS Workflow
- Start with semantic structure: `header`, `main`, `section`, `article`, `footer`.
- Add skip links when there’s a nav/header: `a.skip-nav` → `#main`.
- Define theme tokens up top:
  ```css
  :root {
    --bg: #0b0c10;
    --surface: #10131a;
    --fg: #f7f2e9;
    --muted: #9aa1b5;
    --accent: #ff6b4a;
    --radius: 18px;
    --shadow: 0 18px 40px rgba(0,0,0,0.35);
    --ease: cubic-bezier(.2,.9,.2,1);
  }
  ```
- Layout: Grid for macro, Flexbox for clusters. Break the grid deliberately but preserve reading order.
- Motion: prefer CSS keyframes + `transition`. Orchestrate a few moments (load + hover), not everything.
- Reveal animations: use IntersectionObserver to toggle classes (small JS only).

## React Patterns (TypeScript)
- Keep components small: `Hero`, `FeatureList`, `SpecsRail`, `CTA`.
- Use data arrays for repeated UI (cards/steps/metrics) and map to components.
- Styling: prefer the repo’s existing approach. If unknown, CSS Modules is a safe default.
- Motion: keep durations < 500ms; stagger by 80–140ms; gate everything with reduced-motion.

## Motion & Reduced Motion
- Always include a reduced-motion fallback:
  ```css
  @media (prefers-reduced-motion: reduce) {
    * { animation-duration: 0.001ms !important; animation-iteration-count: 1 !important; transition-duration: 0.001ms !important; scroll-behavior: auto !important; }
  }
  ```
- Keep animations paint-friendly (transform/opacity). Avoid animating large blur shadows.

## Layout & Spacing
- Use a spacing scale (4/8 base). Keep prose line-length ~68–76ch.
- If maximalist: constrain chaos inside frames. If minimal: increase margins and tighten hierarchy.

## Texture & Backgrounds (CSS-only)
- Use layered gradients and subtle noise-like patterns (no heavy assets by default).
- Prefer subtle grain overlays over full-page blur.

## QA Checklist
- One `h1`, logical heading order, landmarks present.
- Focus visible, keyboard usable, contrast AA+.
- Tap targets 44px minimum for primary interactions.
- Mobile layout feels intentional (not just “stack everything”).

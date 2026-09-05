# React + Next.js Best Practices (concise)

## React component design
- Keep components and Hooks pure: compute UI from props/state without side effects in render.
- Run side effects outside render (event handlers or Effects).
- Prefer event handlers for user-driven work; avoid “Effect for everything.”
- Follow the Rules of Hooks (top-level only; consistent call order; only in React functions).
- Don’t call component functions directly; use JSX so React controls rendering.
- Split large components into small, focused pieces; keep props stable and typed.

## Next.js App Router
- Server Components by default; add `"use client"` only when you need state, effects, or browser APIs.
- Keep Client Components small and isolated (UI islands).
- Use `next/link` for navigation and `next/image` for real images.
- Prefer `next/font` when changing typography to avoid layout shift/FOIT.

## Data fetching and caching
- Prefer server-side `fetch` and explicit caching with `cache`/`revalidate`.
- Avoid client-side fetch inside `useEffect` unless you truly need client-only data.
- Keep loading/error states explicit and user-friendly.

## State and performance
- Minimize global state; lift only when necessary.
- Memoize expensive calculations; keep derived state computed, not stored.
- Avoid unnecessary re-renders by splitting components and using stable props.

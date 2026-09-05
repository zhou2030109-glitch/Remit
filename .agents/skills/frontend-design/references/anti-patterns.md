# Anti-Patterns (avoid “AI slop”)

Use this as a quick filter before shipping.

## Visual clichés
- Generic purple-on-white gradients with soft glass cards everywhere.
- Samey startup landing layout: centered hero, 3 cards, logo wall, FAQ, identical CTA band.
- Indiscriminate glow + blur layers that reduce legibility.

## Typography defaults
- Unintentional type: no scale, no rhythm, headings too close in size to body.
- Mixing too many fonts “because it looks cool” (unless the direction is explicitly chaotic).
- Defaulting to the same trendy “safe” font pairings (e.g., the usual modern grotesks) when the project calls for a stronger voice.

## Motion mistakes
- Random micro-animations everywhere instead of one orchestrated sequence.
- No `prefers-reduced-motion` handling.
- Hover-only affordances (mobile users get nothing).

## UX/a11y misses
- Weak focus states, low contrast, tiny hit targets.
- Buttons styled as links (or vice versa) without correct semantics.
- Missing labels on inputs, icon-only actions without accessible names.

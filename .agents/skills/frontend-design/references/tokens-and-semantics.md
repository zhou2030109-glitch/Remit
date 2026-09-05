# Tokens and Semantics (strict vs flexible)

Use tokens to keep designs bold *and* consistent. Avoid hard-coded colors/sizes in component CSS.

## Strict rules (do not break)
- **Semantic colors are meaning**: error/success/warning/info are reserved for status and feedback, not decoration.
- **Readable type ramp**: use a small, named scale (Caption/Body/Title/Display). Avoid arbitrary font sizes.
- **Sentence case UI**: avoid ALL CAPS for body/UI labels unless a brand explicitly requires it.
- **Dark mode is first-class**: design tokens must work in light + dark; meet contrast in both.
- **One primary CTA per view**: the main action is visually dominant; everything else is secondary/tertiary.
- **Visible focus**: keyboard focus must be obvious; don’t remove outlines without replacement.

## Flexible knobs (tune per product)
- **Brand accent hue**: can change per project, but keep usage sparse.
- **Surface temperature**: cooler vs warmer neutrals in dark mode is OK if contrast holds.
- **Density preset**:
  - **Comfy**: larger spacing, longer line-height, fewer items per view.
  - **Compact**: tighter spacing for dashboards/tables (never cramped typography).

## Minimal token set (recommended)
Color:
- `--bg`, `--surface`, `--surface-2`
- `--ink`, `--muted`
- `--accent`
- `--danger`, `--warning`, `--success`, `--info`

Type:
- `--font-sans`, `--font-mono`
- `--text-xs`, `--text-sm`, `--text-md`, `--text-lg`, `--text-xl`, `--text-2xl`
- `--lh-tight`, `--lh-body`

Motion:
- `--dur-1`, `--dur-2`, `--dur-3`
- `--ease-out`

## When to upgrade tokens
If you add more than ~2 components with custom colors/sizes, promote them into tokens.

# Design

## Theme
Warm systems-engineering field manual. Light, crisp paper; inked typography;
clay/coral as the single brand accent; one dark contrast band where the animated
pipeline lives. The opposite of a dark admin panel. Uniform across the marketing
pages and the live console.

## Color (OKLCH — see `frontend/theme.css` for the authoritative tokens)
- Surfaces: `--paper` 0.984/0.006/56 (crisp warm white, above the cream band),
  `--paper-2` alt sections, `--surface` lifted white, `--ink-deep` 0.205 dark band.
- Ink: `--ink` 0.245, `--ink-2` 0.405 (body), `--ink-3` 0.520 (labels/large only).
- Brand: `--clay` 0.665/0.145/42 (fills), `--clay-ink` 0.470 (accent text ≥4.5:1),
  `--clay-soft` tint.
- Semantic (each base + `-ink` text variant): green=savings, blue=model, amber=cache,
  red=blocked, violet=router.
- Strategy: Restrained-to-Committed. Clay carries identity; neutrals are warm, never
  beige-default. Color is also semantic in the console (each served-from state).

## Typography
- One family, committed weight contrast: **Bricolage Grotesque** (200–800 variable,
  self-hosted) for display, headings, and UI. Distinctive + warm; deliberately not
  Inter/DM/Plex.
- **JetBrains Mono** (self-hosted) for genuine machine data only — token counts,
  costs, latencies, model ids, trace lines. Never decorative.
- Scale: fluid `clamp()`, ratio ≥1.25, display ≤5.4rem, heading tracking -0.03em.

## Motion
- Canvas hero: packets flowing through the gateway (rAF).
- The 9-stage pipeline animates as a living flow chart on the dark band.
- Scroll reveals (IntersectionObserver) enhance already-visible content; staggered,
  not a uniform reflex. 3D pointer-tilt on the hero diagram and feature cards.
- Easing: ease-out quint `cubic-bezier(0.22,1,0.36,1)`. No bounce/elastic.
- Every animation has a `prefers-reduced-motion` fallback.

## Layout
- `--maxw` 1200 (1380 wide). Fluid `--pad`. Section rhythm via `.band` / `.band-alt`
  / `.band-deep`. Asymmetry over uniform card grids; cards only where they're the
  right affordance, never nested.

## Bans honored
No gradient text, no side-stripe borders, no default glassmorphism, no hero-metric
template, no identical card grids, no per-section eyebrow. Numbered markers appear
ONLY on the real 9-step pipeline (an actual ordered sequence).

## Pages
- `/` landing — `frontend/index.html` + `landing.css` + `landing.js`
- `/about` — `frontend/about.html`
- `/console` — the live dashboard — `frontend/console.html` + `console.css` + `app.js`
- Shared: `frontend/theme.css`, `frontend/fonts/*`

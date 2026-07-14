# DESIGN.md — ul_grades

Design brief for the visual layer of this app. Read this before touching
anything in `static/` or `templates/`.

---

## 0. What this app is

A grade monitor for students at the Lebanese University Faculty of Engineering.
The single most important thing on any screen is **a number in a table**.

This is a **data app**, not a landing page. Every design decision serves
legibility and density of numeric data. If a decision does not do that, it is
decoration, and decoration is out of scope.

**Identity:** the app should read as belonging to ULFG. That is carried by the
palette (blue + gold) and nothing else. We are **not** imitating the layout,
typography, or structure of the ULFG website.

---

## 1. Anti-patterns — DO NOT DO THESE

This list is not optional. The current design fails on most of these. Every item
here is present in the codebase today and must be removed.

- **No decorative blurred orbs.** Delete the `pointer-events-none fixed inset-0`
  div in `base.html` containing the three `blur-3xl` circles. Entirely.
- **No gradient backgrounds.** Not on the body, not on buttons, not anywhere.
  Delete the `bg-[radial-gradient(...)]` on `<body>`. Delete the
  `linear-gradient(90deg, #53d6b7, #70a5ff)` on `.btn-primary`.
- **No box-shadows.** Zero. Not on cards, not on the table wrap, not on stats.
  Delete `.shadow-glow` and the `boxShadow.glow` Tailwind extension.
  Depth comes from a 1px border and a surface tint. Nothing else.
- **No `backdrop-blur`.** Delete it from the header.
- **No glassmorphism.** No `bg-white/5`, no `border-white/10`, no
  `rgba(255,255,255,0.05)` surfaces. Surfaces are solid colors.
- **No ambient animation.** Delete the `floaty` and `pulseGlow` keyframes from
  the Tailwind config, and the `pulse-card` keyframe from `app.css`. Nothing on
  this page loops forever.
- **No uppercase letter-spaced eyebrows.** Delete the `.eyebrow` class.
- **No emoji as icons.**
- **No three-column feature-card grid.**
- **No centered hero.**
- **More than 2 border-radius values anywhere is a bug.**
- **More than 4 font sizes on one screen is a bug.**

---

## 2. Color

### Source of truth

There is currently a conflict: Tailwind config defines one palette, `app.css`
`:root` defines a second, and templates hardcode a third (`bg-white/5`).
**Collapse to one.** Define the palette in the Tailwind config, mirror it into
CSS custom properties for `app.css`, and never hardcode a color in a template.

### Palette

Light theme. `color-scheme: light`.

```css
/* Surfaces */
--bg:         #FFFFFF;   /* page background, flat */
--panel:      #F7F8FA;   /* cards, table header row */
--panel-hover:#F1F3F6;   /* table row hover */
--line:       #E3E6EB;   /* all 1px borders */
--line-hard:  #C8CDD6;   /* dividers that must be visible */

/* Text */
--text:       #0F1720;   /* primary */
--text-mid:   #4A5563;   /* secondary */
--text-mute:  #7A8494;   /* labels, meta, table headers */

/* Brand — identity only */
--brand-blue: #4A6BA5;   /* TODO: replace with picked hex from ULFG logo */
--brand-gold: #EDB61E;   /* TODO: replace with picked hex from ULFG logo */

/* UI blue — derived from brand hue, saturated for interaction */
--accent:      #2C55A0;  /* buttons, links, active nav. ~7:1 on white. */
--accent-hover:#234784;
--accent-soft: #EDF2FB;  /* active nav bg, subtle highlight */
--accent-ring: rgba(44, 85, 160, 0.25);  /* focus ring */

/* Gold — fill and rules only, NEVER text on white */
--gold-bar:  #EDB61E;    /* solid 3px rules only. Never a hairline. */
--gold-bg:   #FDF3D3;    /* pale fill for chips / highlighted rows */
--gold-text: #8A6407;    /* dark gold. Readable on white and on --gold-bg. */

/* Grade signals — these colors have ONE job */
--pass: #17803D;
--warn: #8A6407;         /* same as --gold-text */
--fail: #B91C1C;
```

### Color rules

| Color | Its one job | Never used for |
|---|---|---|
| `--accent` (blue) | Anything clickable: buttons, links, active nav, focus ring | Grade values |
| Gold | The header rule; the borderline-grade chip fill | Text on white; hairlines; decoration |
| `--pass` green | A passing grade | Anything else |
| `--fail` red | A failing grade | Anything else |
| Grey | Everything else | |

**The rule that makes this work: if an element is neither clickable nor branded,
it is grey.** No exceptions.

**Contrast:** never gold text on white. `#EDB61E` on `#FFFFFF` is ~1.9:1 and is
illegible. Gold appears as a *fill* behind dark text, or as a *solid thick bar*.

---

## 3. Typography

```css
--font-ui:   'IBM Plex Sans', ui-sans-serif, system-ui, sans-serif;
--font-num:  'IBM Plex Mono', ui-monospace, monospace;
```

Load from Google Fonts. Weights needed: Plex Sans 400 / 500 / 600, Plex Mono
400 / 500.

**Why:** Plex was designed as an engineering/institutional face. It has more
character than Inter, which is the default everyone reaches for. Plex Mono gives
true tabular figures, which is the whole ballgame for a grades table.

### Type scale — exactly four sizes

```css
--fs-display: 28px;  /* the GPA. Nothing else on any page is this size. */
--fs-lg:      18px;  /* card titles, page heading */
--fs-base:    14px;  /* body text, table cells */
--fs-sm:      12px;  /* labels, table headers, meta */
```

Weights: 600 for headings, 500 for labels and table headers, 400 for body.
That's three weights. No more.

If you find yourself wanting a fifth size, the answer is no.

### Numbers — this is the priority

Every number in the app (grades, credits, GPA, averages, ranks) uses:

```css
font-family: var(--font-num);
font-variant-numeric: tabular-nums;
text-align: right;
```

Numeric table columns are **right-aligned**. Text columns are left-aligned.
This is non-negotiable. It is the single thing that makes a data app look
designed rather than generated.

---

## 4. Geometry

```css
--r:      6px;    /* cards, inputs, buttons, table wrap, alerts. Everything. */
--r-pill: 999px;  /* badges, nav pills. */
```

Two values. That is the whole radius system. The current code has six.

**Spacing:** 4px base unit. All padding and margin are multiples of 4.
Prefer 8 / 12 / 16 / 24. Avoid anything that isn't on the grid.

**Borders:** 1px `var(--line)`. This is the only depth mechanism in the app.

---

## 5. Components

### Header (the signature element)

The one place identity lives:

- Solid `--brand-blue` bar, full width, white text.
- A **3px solid `--gold-bar` rule along its bottom edge.**
- ULFG logo at ~28px on the left, next to "UL Grade Monitor".
- Nav on the right. Active item: white text with a 2px gold underline.
- Padding: `12px 24px`. Tighter than it is now.
- No blur, no shadow, no rounded corners on the bar itself.

That blue bar with the gold rule under it is the app's signature. It is the
*only* large area of color on any page. Everything else is white and grey.
Spend the boldness here and nowhere else.

### Tables (the real work)

This is what the app is for. Get it right.

- Header row: `--panel` background, `--fs-sm`, weight 500, `--text-mute`.
  Sentence case, **not** uppercase.
- Row padding: `8px 12px`. Dense. This is a table, not a poster.
- Row separator: 1px `--line` on the top of each `td`.
- Row hover: `--panel-hover`. No transform, no shadow, no scale.
- Numeric columns right-aligned with `--font-num` and `tabular-nums`.
- A borderline grade gets a `--gold-bg` chip with `--gold-text` text.
- A failing grade gets `--fail` text. A pass gets `--pass` text.
- No zebra striping. The hairline rules are enough.

### Cards

```
background: var(--panel);
border: 1px solid var(--line);
border-radius: var(--r);
padding: 16px;
box-shadow: none;
```

### Buttons

- Primary: solid `--accent` background, white text, `var(--r)`. **Flat.**
- Secondary: white background, 1px `--line`, `--text` text.
- Danger: white background, 1px `--fail`, `--fail` text.
- No gradients. No transform on `:active`. Just a background change on hover.
- Focus: `box-shadow: 0 0 0 3px var(--accent-ring)`. Visible keyboard focus is
  required on every interactive element.

### Hidden grades

The current `.is-hidden-grade` uses `filter: blur(5px)` on light text over a
dark background. On white this will need re-tuning. Prefer replacing the blur
with a simple grey em-dash placeholder or a `--panel` block. Blur on white
tends to look like a rendering bug.

---

## 6. Motion

One animation is permitted in the entire app:

> When a grade changes, its row flashes `--accent-soft` and fades back to
> transparent over 400ms. Once. Not twice, not looping.

```css
@media (prefers-reduced-motion: reduce) {
  /* disable it */
}
```

Everything else: instant, or a 150ms color transition on hover. No transforms.
No glows. No levitation.

---

## 7. Density

This is a data app. It should feel information-dense, not airy.

- Header: `py-3`, not `py-4`.
- Cards: `16px` padding, not `24px`.
- Table rows: `8px` vertical, not `14px`.
- The page should show more rows without scrolling than it does today.

---

## 8. Build note (not design, but do it)

`base.html` loads Tailwind from `https://cdn.tailwindcss.com`. That is the
development-only build. It compiles CSS in the browser at runtime, which causes
a flash of unstyled content and a slow first paint. Move to the Tailwind CLI
build before this is shown to anyone.

---

## 9. Order of work

Do these in sequence. Check the browser after each step. Do not restyle
everything in one pass or you will not know what caused what.

1. **Strip.** Delete the orb div, the radial gradient, `backdrop-blur`, every
   `box-shadow`, `.shadow-glow`, `.eyebrow`, the `floaty` / `pulseGlow` /
   `pulse-card` keyframes. Do this *before* recoloring, so you are not
   recoloring things you are about to delete.
2. **Invert.** `color-scheme: light`. Replace the whole token layer with the
   palette in §2. Strip every `bg-white/N` and `border-white/N` from templates.
   Expect ghost elements; hunt them down.
3. **Flatten.** Collapse radius to two values, type to four sizes.
4. **Identity.** Build the header: blue bar, gold rule, logo.
5. **Tables.** Plex Mono, tabular figures, right-aligned numerics, tighter rows,
   grade signal colors.
6. **Audit.** Walk every page. Find anything that is blue but not clickable,
   or gold but not a warning. Fix it.
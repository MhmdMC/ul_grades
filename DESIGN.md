# DESIGN.md — ul_grades

Binding design spec for the visual layer. Read before touching anything in
`static/` or `templates/`.

---

## 0. What this app is

A grade monitor for students at the Lebanese University Faculty of Engineering.
The most important thing on any screen is **a number in a table**.

This is a **data app**, not a landing page. Every decision serves legibility and
density of numeric data. If a decision does not do that, it is decoration, and
decoration is out of scope.

**Identity:** the app reads as ULFG through **palette only** (blue + gold). We
are not imitating the layout, typography, or structure of the ULFG website.

---

## 1. Anti-patterns — binding, not advisory

- No decorative blurred orbs. No `blur-3xl` circles.
- No gradient backgrounds. Not on `body`, not on buttons, not anywhere.
- No `box-shadow`. Zero. Depth = 1px border + surface tint. Nothing else.
- No `backdrop-blur`.
- No glassmorphism: no `bg-white/N`, no `border-white/N`, no
  `rgba(255,255,255,0.05)` surfaces. Surfaces are solid.
- No ambient or looping animation.
- No uppercase letter-spaced eyebrows.
- No emoji as icons.
- **No card grids for rectangular data.** See §6. This is the big one.
- More than 2 border-radius values anywhere is a bug.
- More than 4 font sizes on one screen is a bug.

---

## 2. Color

Light theme. `color-scheme: light`.

**One source of truth.** Define the palette in the Tailwind config, mirror it as
CSS custom properties for `app.css`. Never hardcode a color in a template.

```css
/* Surfaces */
--bg:          #FFFFFF;  /* page background, flat */
--panel:       #F7F8FA;  /* cards, table header row, code chips */
--panel-hover: #F1F3F6;  /* table row hover */
--line:        #D8DCE2;  /* all 1px borders */
--line-hard:   #B6BCC6;  /* dividers that must be seen */

/* Text */
--text:        #0F1720;
--text-mid:    #4A5563;
--text-mute:   #7A8494;  /* labels, table headers, meta, em-dashes */

/* Blue — the ULFG brand blue. There is exactly one blue.
   It passes contrast as a UI color, so brand and interaction are the same
   token: header bar, buttons, links, active nav. */
--accent:       #3D63A7;  /* 5.9:1 with white text */
--accent-hover: #325288;
--accent-soft:  #EDF1F8;  /* active nav bg, row flash */
--accent-ring:  rgba(61, 99, 167, 0.3);

/* Gold — the ULFG brand gold. Fill and rules only. NEVER text on white. */
--gold-bar:  #E2B105;  /* solid 3px rules only. Never a hairline. */
--gold-bg:   #FCF2CE;  /* pale chip fill */
--gold-text: #8A6A04;  /* dark gold. Readable on white and on --gold-bg. */

/* Grade signals — one job each */
--pass: #17803D;
--warn: #8A6A04;
--fail: #B91C1C;
```

### Color rules

| Color | Its one job | Never |
|---|---|---|
| `--accent` blue | The header bar; anything clickable | Grade values |
| Gold | Header rule; borderline-grade chip fill | Text on white; hairlines; decoration |
| `--pass` | A passing grade | Anything else |
| `--fail` | A failing grade | Anything else |
| Grey | Everything else | |

**The rule that makes this work: if an element is neither clickable nor branded,
it is grey.** Course names are grey. Headings are grey. Stat labels are grey.

Gold on white is ~1.9:1 and illegible. Gold is a *fill* behind dark text, or a
*solid thick bar*. Never a text color, never a 1px border.

---

## 3. Typography

```css
--font-ui:  'IBM Plex Sans', ui-sans-serif, system-ui, sans-serif;
--font-num: 'IBM Plex Mono', ui-monospace, monospace;
```

Plex is an engineering/institutional face with more character than Inter (the
default everyone reaches for). Plex Mono gives true tabular figures, which is
the whole point for a grades app.

### Type scale — exactly four sizes

```css
--fs-display: 28px;  /* the partial average and partial rank. Nothing else. */
--fs-lg:      18px;  /* page headings, card titles */
--fs-base:    14px;  /* body, table cells */
--fs-sm:      12px;  /* labels, table headers, meta */
```

Weights: 600 headings, 500 labels and table headers, 400 body. Three weights.

There is no fifth size. `text-3xl` and `text-4xl` are bugs.

### Numbers — the priority

Every number in the app (grades, credits, ranks, averages) uses:

```css
font-family: var(--font-num);
font-variant-numeric: tabular-nums;
text-align: right;
```

Numeric columns right-aligned. Text columns left-aligned. This is what makes a
data app look designed rather than generated.

---

## 4. Geometry

```css
--r:      6px;    /* cards, inputs, buttons, table wrap, alerts. Everything. */
--r-pill: 999px;  /* badges, nav pills. */
```

Two values. The codebase currently has six.

Spacing: 4px base unit. Prefer 8 / 12 / 16 / 24.
Borders: 1px `--line`. The only depth mechanism in the app.

---

## 5. Components

### Header (the signature element)

- Solid `--accent` bar, full width, white text.
- **3px solid `--gold-bar` rule along its bottom edge.**
- ULFG logo at ~28px, left, beside "UL Grade Monitor".
- Nav right. Active item: white text, 2px gold underline.
- Padding `12px 24px`. No blur, no shadow, no rounded corners on the bar.

This blue bar with the gold rule is the app's signature and the **only** large
area of color on any page. Everything else is white and grey. Spend the boldness
here and nowhere else.

### Tables

One table component, used by both the dashboard and the leaderboard. Do not
create a second table style. Improve `.table` / `.table-wrap` in `app.css` once
and let both pages benefit.

```
.table-wrap   border 1px --line, radius --r, no shadow, overflow-x auto
.table thead  --panel bg, --fs-sm, weight 500, --text-mute, sentence case
              (NOT uppercase)
.table td     padding 8px 12px, border-top 1px --line
.table tbody tr:hover   background --panel-hover. No transform, no shadow.
.table .num   font-family --font-num, tabular-nums, text-align right
```

- No zebra striping. Hairline rules are enough.
- Missing values render as an em-dash in `--text-mute`. **Never the string
  "Not available"** — absent data must not be louder than present data.
- Grade cells take `value-{pass|warn|fail|neutral}`.

### Cards

`--panel` background, 1px `--line`, `--r` radius, 16px padding, no shadow.

Cards are for **non-rectangular** content: a form, a prose block, a single
summary. Cards are **not** for repeated records. See §6.

### Buttons

- Primary: flat `--accent`, white text. **No gradient.**
- Secondary: white, 1px `--line`, `--text`.
- Danger: white, 1px `--fail`, `--fail` text.
- Hover: background change only. No transform on `:active`.
- Focus: `box-shadow: 0 0 0 3px var(--accent-ring)`. Visible keyboard focus is
  required on every interactive element.

### Inline code

Add a single element rule; stop repeating utilities:

```css
code {
  font-family: var(--font-num);
  font-size: 0.9em;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 1px 5px;
}
```

### Meta labels

Replaces the deleted `.eyebrow`:

```css
.meta {
  font-size: var(--fs-sm);
  font-weight: 500;
  color: var(--text-mute);
  /* no uppercase, no letter-spacing */
}
```

---

## 6. The dashboard is a table, not a card grid

**This is the most important section in this document.**

`dashboard.html` currently renders each course as an `<article class="grade-card
card">` containing four nested `.stat` boxes. Three levels of box to display one
number. Four courses fill two screens.

This is wrong, and it is the single biggest reason the app looks generated. When
a design doesn't know what its data is, it puts everything in a card.

Course data is **perfectly rectangular** — every course has the same fields.
Rectangular data goes in a table. The card layout destroys the one thing that
makes this data useful: you cannot compare ranks across courses, because the eye
must jump around a 2D grid instead of scanning one column. It also repeats every
label once per course, where a table writes each label once, in the header.

### The replacement

Delete `<div id="cards">` and the entire `<article class="grade-card card">`
loop. Replace with one `<table class="table">` inside a `.table-wrap`:

```
| Course | Code | Cr | Partial | Partial rank | Final | Final rank | To pass |
```

- One `<tr>` per course. All courses visible without scrolling.
- `Cr`, `Partial`, `Partial rank`, `Final`, `Final rank` use `.num` (mono,
  tabular, right-aligned).
- `Course`, `Code`, `To pass` are left-aligned.
- `Course` name is `--text`. It is not clickable, so it is not colored.
- Partial cell keeps `value-{{ course.partial_color }}`.
- Uses the existing `.table` / `.table-wrap` classes. No new table styles.

### The final grade splits into TWO columns

In `app.py`:

```python
"final_label": "Final grade" if final_grade is not None else "Grade to pass",
"final_value": final_grade if final_grade is not None else grade_to_pass(...),
```

So `final_value` holds **two different quantities** depending on `final_label`:

- `"Final grade"`   -> an actual grade (numeric, e.g. `85.0`)
- `"Grade to pass"` -> a projection (text, e.g. `"Need 15.00"`, `"Already passed (-1.50)"`)

**A column has one type. Do not merge these into one column.** Split them:

| Column | Populated when | Type | Else |
|---|---|---|---|
| **Final** | `final_label == "Final grade"` | numeric, `.num`, `value-{{final_color}}` | em-dash |
| **To pass** | `final_label == "Grade to pass"` | text, left, `--text-mid` | em-dash |

They are mutually exclusive; no row ever has both. This makes it visible at a
glance which courses have finals posted and which do not — information the card
layout actively hid.

```jinja
{% if course.final_label == 'Final grade' %}
  <td class="num value-{{ course.final_color }}">{{ course.final_value }}</td>
  <td class="muted">&mdash;</td>
{% else %}
  <td class="muted">&mdash;</td>
  <td>{{ course.final_value }}</td>
{% endif %}
```

**TECH DEBT — do NOT fix during the restyle.** The template branches on a
display string. `app.py` should expose a boolean `has_final` instead. Note it,
move on. One kind of change at a time.

### CRITICAL: preserve the JS hooks

`static/js/app.js` uses Socket.IO to live-update grades. It locates courses via
`data-course-key` and toggles `is-changed` / `is-removed` on `.grade-card`.

When converting to a table, **both must move to the row**:

```html
<tr class="grade-row" data-course-key="{{ course.key }}">
```

Then update `app.js` to query `.grade-row` instead of `.grade-card`, and update
the CSS so `.grade-row.is-changed` flashes the **row background** rather than
animating a card.

**If the markup is converted and the JS is not, live grade updates silently stop
working, and nobody notices until a grade changes.** Verify after conversion.

### The four top-level stats

May stay as a 4-across strip. But flatten them:

- Label: `--fs-sm`, `--text-mute`.
- Value: `--font-num`, tabular-nums.
- The partial average and the partial rank are the `--fs-display` numbers, and
  they are sized as a matched pair. **Nothing else on any page is.** The two
  final stats stay `--fs-lg` until finals exist.
- Missing renders as an em-dash, not "Not available".

---

## 7. Motion

One animation is permitted in the whole app:

> When a grade changes, its row flashes `--accent-soft` and fades back over
> 400ms. Once. Not twice. Not looping.

Wrap it in `@media (prefers-reduced-motion: reduce)`.

Everything else: instant, or a 150ms color transition on hover. No transforms.
No glows. No levitation.

---

## 8. Density

- Header `py-3`.
- Cards 16px padding.
- Table rows 8px vertical.
- The dashboard should show every course without scrolling.

If a table feels cramped, add **horizontal** padding. Vertical air is what makes
a table look like a landing page.

---

## 9. Global utility map

Apply to every template. This is a mechanical lookup, not a design decision.

```
text-slate-100 / -200        -> --text
text-slate-300               -> --text-mid
text-slate-400 / -500        -> --text-mute
text-cyan-*   (on a link)    -> --accent
text-cyan-*   (not a link)   -> --text     (not clickable = not colored)
bg-slate-900 / -950 (chips)  -> use the `code` element rule
bg-slate-950/N (overlays)    -> bg-white or --panel, re-tune opacity
border-white/N               -> --line
bg-white/N                   -> --panel, or nothing
backdrop-blur-*              -> delete
shadow-glow / shadow-*       -> delete
rounded-xl / -2xl / -3xl     -> --r (6px)
text-3xl / text-4xl          -> --fs-lg (18px), unless it is the partial average
"Not available"              -> em-dash in --text-mute
```

---

## 10. Known offenders

Confirmed present. Fix all of them.

- `.btn-primary` — `linear-gradient(90deg, #53d6b7, #70a5ff)`. Flatten to
  `--accent`.
- `share.html:97` — sticky bar: `rounded-2xl border-white/10 bg-slate-950/80
  shadow-glow backdrop-blur-xl`. Full rebuild.
- `dashboard.html:17` — enter-overlay: `bg-slate-950/95`, `backdrop-blur-xl`,
  `text-4xl` heading. Restyle to a white overlay, `--panel` card, `--fs-lg`
  heading. **Keep the feature** — it exists because browsers block autoplay
  audio without a user gesture.
- `help.html:4` — `backdrop-blur-xl` on the card.
- `help.html` — two `<code>` chips with `bg-slate-900`; use the `code` rule.
- `cookie.html:29` — `md:border-white/10`.
- `leaderboard.html` — rank column is left-aligned with a `#` prefix
  (right-align, mono, drop the `#`, the header already says "Rank"); the grade
  column is `text-lg font-semibold` on every row (drop to `--fs-base` / 500);
  `text-cyan-200` profile link becomes `--accent`.

---

## 11. Build note

`base.html` loads Tailwind from `https://cdn.tailwindcss.com`. That is the
development-only build: it compiles CSS in the browser at runtime, causing a
flash of unstyled content and a slow first paint. Move to the Tailwind CLI build
before showing this to anyone.

---

## 12. Order of work

One step per session. Commit after each. Check the browser after each.

1. **Strip.** All decoration deleted. (§1)
2. **Invert.** `color-scheme: light`, new token layer, apply the utility map (§9)
   across all templates.
3. **Flatten.** Radius to 2 values. Type to 4 sizes. Load IBM Plex.
4. **Identity.** Header: blue bar, gold rule, logo.
5. **Table component.** Fix `.table` / `.table-wrap` in `app.css`. Apply to
   `leaderboard.html` first — it is the safer test case, with no Socket.IO
   wiring to break.
6. **Dashboard conversion.** Card grid to table, per §6. Move the JS hooks.
   Verify live updates still fire.
7. **Sweep.** Remaining templates against §9, one file at a time.
8. **Audit.** Run the greps in §13. Zero hits, or you are not done.

---

## 13. Audit

Claude Code cannot see the rendered page. It will report success on work it did
not finish. These greps are the objective test; run them yourself.

```powershell
Select-String -Path templates\*.html -Pattern "slate-|cyan-|white/|backdrop-blur|shadow|gradient|text-3xl|text-4xl|rounded-2xl|rounded-3xl|Not available"

Select-String -Path static\css\app.css -Pattern "gradient|box-shadow|blur|rgba\(255"
```

Zero hits on both means the conversion is complete.

Then **look at every page in the browser yourself.** The grep catches what is
written. Only your eyes catch what is wrong.
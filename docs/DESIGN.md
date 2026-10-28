# Agent2B Console — Design System

> This is twotrack's own console design system: the tokens, type, components and patterns the
> web console in `web/` is built from. It values:
>
> - **A quiet, true-neutral work surface.** Greys have equal R, G and B, and colour appears
>   only when it means something (§1).
> - **One accent.** A single blue is the identity colour; green, amber and red carry state and
>   risk only.
> - **Honest data.** Provenance, freshness and the numeric-lock disclosure stay on screen, kept
>   present but quiet.
> - **A calm AI.** Thinking is three monochrome dots, and answers sit on the page, not in cards.
> - **Hierarchy by lightness.** Three text tiers (`foreground` → `muted-foreground` → `faint`)
>   carry rank, not colour.
>
> Stack (`web/package.json`, `web/components.json`): Next.js 16 and React 19 (versions pinned
> in `package.json`), Tailwind CSS 4 (`@theme inline`, CSS variables), shadcn/ui primitives on
> Radix (shadcn settings: `style: "radix-nova"`, `baseColor: "mist"`, `cssVariables: true`,
> `iconLibrary: "lucide"`, `menuColor: "inverted"`, `menuAccent: "bold"`), `lucide-react`,
> `tw-animate-css`, `sonner`, `next-themes`. English-first UI with a `zh` toggle (`next-intl`).
> Patterns in §10 that are specified but not built in `web/` yet are listed in Appendix B.

---

## 0. Quick Reference

| Token | Light | Dark | Role |
|---|---|---|---|
| `--background` | `#fafafa` | `#1a1a1a` | Page ground |
| `--foreground` | `#1a1a1a` | `#ebebeb` | Primary text, "ink" buttons |
| `--card` / `--popover` | `#ffffff` | `#242424` | Raised surfaces |
| `--primary` | `#2f6bed` | `#5b8cf5` | The single accent: CTAs, links, focus |
| `--secondary` / `--muted` | `#f0f0f0` | `#2e2e2e` | Secondary fills, user bubble, chips |
| `--muted-foreground` | `#5c5c5c` | `#a3a3a3` | Secondary text |
| `--faint` | `#6a6a6a` | `#999999` | Tertiary text: captions, provenance, meta |
| `--accent` | `#e8effc` | `#23324e` | Blue tint: hover/selected/info, KPI tiles |
| `--accent-foreground` | `#2a5fd4` | `#9db8f5` | Text on accent |
| `--border` | `#e5e5e5` | `#333333` | Dividers |
| `--success` | `#15803d` | `#46b877` | OK / up / committed |
| `--warning` | `#b45309` | `#d99a4e` | Caution / pending / partial |
| `--destructive` | `#dc2626` | `#f0616d` | Error / down / dangerous |
| `--radius` | `0.75rem` (12px) | — | Base radius; scale derives from it |
| Root font size | `16px` | — | The one global scale knob (`--font-size`, §3.2) |
| Easing | `cubic-bezier(0.4, 0, 0.2, 1)` | — | `--ease-standard` |

---

## 1. Visual Theme & Atmosphere

**One-line philosophy:** a quiet, true-neutral work surface where color only appears when it
*means* something.

- **Mood:** calm, dense, professional B2B. Reads like a well-kept ledger, not a dashboard
  showpiece. Restraint over decoration; information density over hero moments.
- **Neutrals are strictly neutral (R = G = B).** No grey carries a warm or cool tint. Do not
  add one to the grey ramp.
- **Single accent, functional color.** One blue (`--primary`) is the only identity color.
  The four semantic colors (success / warning / destructive / info) exist because they carry
  meaning (state, risk, up/down); they are never used decoratively.
- **Hairline rings instead of borders.** Surfaces are separated by `ring-1 ring-foreground/10`
  (foreground at 10% alpha) rather than solid grey borders, so edges stay correct in both
  themes without per-theme border tokens.
- **Serif accents, sans workhorse.** A bookish display serif is reserved for a handful of
  "title" moments (page title, empty-state greeting, report H1, wordmark). Everything else is
  a clean sans.
- **Hierarchy by lightness.** Within monochrome areas (reasoning trace, metadata, captions),
  hierarchy is carried by three text tiers — `foreground` → `muted-foreground` → `faint` — not
  by color.
- **The AI is calm.** Thinking is shown as three monochrome dots in the current text color,
  never as colored spinners or orbs. Answers sit directly on the page background, not in cards.
- **Honest data.** Every number on screen can be traced (provenance line), freshness is never
  faked, and post-hoc numeric verification is disclosed. The visual language is built to make
  this metadata present but quiet.

---

## 2. Color Palette & Roles

### 2.1 Core tokens (shadcn semantic set)

| Token | Light | Dark | Role |
|---|---|---|---|
| `--background` | `#fafafa` | `#1a1a1a` | Page ground |
| `--foreground` | `#1a1a1a` | `#ebebeb` | Body text, headings, ink-button fill |
| `--card` | `#ffffff` | `#242424` | Cards, tables, composer |
| `--card-foreground` | `#1a1a1a` | `#ebebeb` | |
| `--popover` | `#ffffff` | `#242424` | Menus, popovers, dialogs, toasts |
| `--popover-foreground` | `#1a1a1a` | `#ebebeb` | |
| `--primary` | `#2f6bed` | `#5b8cf5` | Primary buttons, links, focus ring, chart-1 |
| `--primary-foreground` | `#ffffff` | `#12151b` | Text on primary |
| `--secondary` | `#f0f0f0` | `#2e2e2e` | Secondary buttons, user bubble, filter chips |
| `--secondary-foreground` | `#1a1a1a` | `#ebebeb` | |
| `--muted` | `#f0f0f0` | `#2e2e2e` | Skeletons, table hover (`/50`), tab list |
| `--muted-foreground` | `#5c5c5c` | `#a3a3a3` | Secondary text, descriptions, table headers |
| `--faint` | `#6a6a6a` | `#999999` | Tertiary text (captions, provenance, timestamps) |
| `--faint-foreground` | `#6a6a6a` | `#999999` | Alias of `--faint` |
| `--accent` | `#e8effc` | `#23324e` | Selected rows, menu focus, clarify chips, KPI tiles |
| `--accent-foreground` | `#2a5fd4` | `#9db8f5` | Text on accent |
| `--border` | `#e5e5e5` | `#333333` | Dividers, table rules |
| `--input` | `#e5e5e5` | `#383838` | Control borders |
| `--input-background` | `#ffffff` | `#242424` | Fill of `Input` and `NativeSelect` (the composer's textarea is transparent, 4.3) |
| `--switch-background` | `#d4d4d4` | `#383838` | Switch track (off) |
| `--ring` | `#2f6bed` | `#5b8cf5` | Focus ring (used at `/50`) |

### 2.2 Semantic state colors

Solid token + paired foreground, light/dark. Soft fills are produced with opacity
(`bg-success/10`, dark `bg-success/20`) — **no separate "soft" tokens**.

| Token | Light | Dark | Foreground (L / D) | Meaning |
|---|---|---|---|---|
| `--success` | `#15803d` | `#46b877` | `#ffffff` / `#12151b` | OK, committed, healthy, up |
| `--warning` | `#b45309` | `#d99a4e` | `#ffffff` / `#12151b` | Caution, partial, needs attention, pending |
| `--destructive` | `#dc2626` | `#f0616d` | `#ffffff` / `#1a1a1a` | Error, rejected, dangerous action, down |
| `--info` | `#2f6bed` | `#5b8cf5` | `#ffffff` / `#12151b` | Neutral notice (= primary) |

Tone mapping used across status dots, pills, gauges, text:

| Tone | Dot / fill | Text | Pill |
|---|---|---|---|
| green | `bg-success` | `text-success` | `bg-success/10 text-success` |
| yellow | `bg-warning` | `text-warning` | `bg-warning/10 text-warning` |
| red | `bg-destructive` | `text-destructive` | `bg-destructive/10 text-destructive` |
| neutral | `bg-muted-foreground` | `text-muted-foreground` | `bg-muted text-muted-foreground` |

### 2.3 Sidebar tokens

| Token | Light | Dark | Note |
|---|---|---|---|
| `--sidebar` | `#f7f7f7` | `#1f1f1f` | One step off the page ground |
| `--sidebar-foreground` | `#5c5c5c` | `#a3a3a3` | |
| `--sidebar-primary` | `#2f6bed` | `#5b8cf5` | |
| `--sidebar-primary-foreground` | `#ffffff` | `#12151b` | |
| `--sidebar-accent` | `#ebebeb` | `#2e2e2e` | **Neutral** active pill (not blue) |
| `--sidebar-accent-foreground` | `#1a1a1a` | `#ebebeb` | |
| `--sidebar-border` | `#e5e5e5` | `#333333` | |
| `--sidebar-ring` | `#2f6bed` | `#5b8cf5` | |

### 2.4 Data-viz palette

Charts encode data, not state — keep them separate from the semantic set.

| Token | Light | Dark |
|---|---|---|
| `--chart-1` | `#2f6bed` | `#5b8cf5` |
| `--chart-2` | `#6f9bf2` | `#7fa8f5` |
| `--chart-3` | `#38a3d1` | `#56b4dd` |
| `--chart-4` | `#737373` | `#a3a3a3` |
| `--chart-5` | `#b4b4b4` | `#6b6b6b` |

### 2.5 AI activity glow

Used only on the chat composer while the agent is working (see §6.4).

| Token | Value |
|---|---|
| `--ai-glow-a` | `#4facfe` |
| `--ai-glow-b` | `#00f2fe` |
| `--ai-glow-grad` | `linear-gradient(130deg, var(--ai-glow-a), var(--ai-glow-b))` |

### 2.6 Contrast guarantees (WCAG 2.x relative luminance, computed from the values above)

These guarantees cover the **text tiers**: `foreground`, `muted-foreground`, `faint` and
`accent-foreground`, drawn in their solid token colour on the grounds named below. They do not
cover semantic-tone text on its own tint or text faded with opacity; the known cases are the
last bullet and Appendix B.

- Light `--faint #6a6a6a`: page 5.18 · card 5.41 · secondary 4.75 · sidebar 5.05 — all ≥ 4.5.
- Dark `--faint #999999`: page 6.11 · card 5.45 · secondary 4.77 · sidebar 5.79 — all ≥ 4.5.
- Light `--accent-foreground #2a5fd4` on `--accent`: 4.93. Dark: 6.48.
- `--faint` on `--accent` is the tightest pair (light 4.68, dark 4.50) → **on accent grounds
  (tinted tiles, selected rows) use `muted-foreground` (5.79 light, 5.08 dark), never `faint`,
  for small text.**
- When changing any text tier, re-check it against **every** ground it can sit on
  (page, card, secondary, sidebar, accent), not just one.
- **Known limit, outside the guarantees above:** four recipes are below the WCAG AA minimum for
  small text in at least one theme. They are the soft tone pills, badges and notices
  (`bg-{tone}/10 text-{tone}`, dark `/20`, in all four tones); the tool id on a clarification
  chip (`opacity-70`, in `tool-label.tsx`); the "· writes" tag on that chip (`opacity-80`, in
  `clarify-chips.tsx`); and the inactive options of the segmented control
  (`text-foreground/60`, in `segmented-control.tsx`). Do not copy them for new small text.
  Raising them is on the [roadmap](../ROADMAP.md); Appendix B lists them with the other open items.

---

## 3. Typography Rules

### 3.1 Families

| Role | Stack | Weights | Use |
|---|---|---|---|
| Sans (body/UI) | `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif` | 400–700, as the system font provides | Everything by default; `--font-heading` = sans |
| Display serif | `var(--font-libre-baskerville), Georgia, "Songti SC", STSong, SimSun, "Noto Serif CJK SC", serif` | Baskerville 400/700; the CJK serif's own | Page titles, empty-state greeting, report H1, wordmark only |
| Mono | `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` | — | Code, IDs, tool names, JSON params, route-source tags |

Notes:
- **Tokens.** `--font-ui` → `font-sans` and `font-heading`, `--font-display` → `font-serif`,
  `--font-code` → `font-mono` (`@theme inline`; the stacks above are the `:root` values).
- **One web font.** Libre Baskerville comes from the npm package `@fontsource/libre-baskerville`
  through `next/font/local` (`web/src/app/layout.tsx`), which sets `--font-libre-baskerville` on
  `<html>`: Latin subset, 400 and 700, preloaded, `font-display: swap`, with a metric-matched
  Times New Roman fallback face while it loads. The files ship inside the build, so `next build`
  needs no network at build or run time, and the browser loads the font from the console's own
  origin (CSP `font-src 'self'`). Characters outside the Latin subset fall through to the next
  family in the stack.
- **Everything else is a system font**, so the `zh` toggle downloads nothing. Each stack lists
  its Latin families first, so English, and the digits and Latin words inside Chinese text, keep
  one design in both languages; the CJK families after them only supply the glyphs the Latin
  ones lack (a CJK family listed first would set English in its own Latin glyphs). The generic
  family comes last.
- CJK members by platform: sans PingFang SC (macOS, iOS), Hiragino Sans GB (older macOS),
  Microsoft YaHei (Windows), Noto Sans CJK SC (Linux); serif Songti SC and STSong (macOS),
  SimSun (Windows), Noto Serif CJK SC (Linux).
- Switch `<html lang>` between `en` and `zh-CN` with the locale toggle (affects CJK glyph
  selection and line breaking). Format numbers with the active locale.

### 3.2 The root scale knob

`html { font-size: var(--font-size) }` with `--font-size: 16px` on `:root`. Tailwind type
**and spacing** are rem-based, so changing the root size scales the whole UI proportionally.
twotrack ships the knob at 16px and has no per-user control for it yet; the intended range is
discrete steps **14 / 15 / 16 (default) / 18 / 20 px** (Appendix B).

**Rule: all font sizes in rem.** Hard-coded `text-[Npx]` does not follow the knob, so
`web/src` has none; non-standard sizes are named rem tokens instead (below). Two arbitrary
sizes in relative units remain, and both follow the knob: `text-[0.8rem]` on the small button
(4.1) and `text-[0.85em]` on a URL shown as text in model-written answers (10.4).

### 3.3 Type scale

| Token / class | Size (at 16px root) | Line height | Weight | Use |
|---|---|---|---|---|
| `text-caption` (custom) | `0.8125rem` / 13px | 1.4 | 500 | Table headers (`tracking-wide`), micro labels, chart axes, trace lines, disclosure toggles |
| `text-xs` | 0.75rem / 12px | 1rem | 400–500 | Badges, provenance line, notices, metadata, hints |
| `text-sm` | 0.875rem / 14px | 1.25rem | 400–500 | Default UI body: tables, cards, buttons, inputs (≥ md) |
| `text-nav` (custom) | `0.9375rem` / 15px | `leading-relaxed` | 400 / 600 active | Sidebar items & group labels, page description, inline error |
| `text-base` | 1rem / 16px | 1.5rem | 600 | Card titles, report H2, inputs on mobile |
| `text-answer` (custom) | `1.0625rem` / 17px | `leading-relaxed` (1.625) | 600 (answer) / 500 (user bubble) | Chat answer prose, user bubble, composer input |
| `text-lg` | 1.125rem / 18px | 1.75rem | 600 | Report H1 (serif), answer H3, wordmark (serif, 700) |
| `text-page-label` (custom) | `1.1875rem` / 19px | — | 600, `tracking-wide` | Page label in the top bar (Hub) |
| `text-xl` | 1.25rem / 20px | 1.75rem | 600 | Answer H2 |
| `text-2xl` | 1.5rem / 24px | 2rem | 600 | Page title (serif, `leading-tight tracking-tight`); KPI value (sans, `tabular-nums`) |
| `text-4xl` | 2.25rem / 36px | 2.5rem | 500 | Chat empty-state greeting (serif, `tracking-tight`) |

Letter-spacing: `tracking-tight` (−0.025em) on serif titles and wordmark; `tracking-wide`
(0.025em) on caption-tier labels and table headers. Body is default.

### 3.4 Prose tiers (rendered markdown)

Two prose recipes; neither uses `@tailwindcss/typography`.

**`.report-prose`** — long-form reports, read fast:
- Body `text-sm leading-relaxed`, paragraphs `my-3 text-muted-foreground`.
- H1 `font-serif text-lg font-semibold mb-3` · H2 `text-base font-semibold mt-8 mb-2` (first
  child `mt-0`) · H3 `text-sm font-semibold mt-5 mb-1.5`.
- Lists `my-3 space-y-2 pl-4 list-disc marker:text-faint`; ordered lists
  `list-decimal pl-6 marker:font-semibold marker:tabular-nums marker:text-muted-foreground`.
- `strong` 600 foreground · `hr my-4` · inline `code` `rounded bg-muted px-1 py-0.5 text-xs font-mono`.
- Blockquote = **conclusion card**: `my-5 rounded-xl border-l-0 bg-accent/60 px-4 py-3
  font-medium text-foreground ring-1 ring-primary/15` (the takeaway must be the most visible
  sentence, never the faintest).
- Tables: `my-3 w-full border-separate border-spacing-0 overflow-hidden rounded-xl ring-1
  ring-border`; `th` `border-b bg-muted/40 px-3 py-2 text-left font-medium
  text-muted-foreground`; `td` `border-b border-border/60 px-3 py-2 text-foreground/90`;
  numeric tables `tabular-nums`. When wrapped in a horizontal scroller, move the outline onto
  the scroller (`rounded-xl border`) so it is not clipped.

**`.answer-prose`** (stacked on `.report-prose`) — chat answers, read sentence by sentence:
- Container `text-answer font-semibold`; `p, li` `font-semibold
  text-foreground` (weight set on the container so it inherits; do not rely on child-selector
  order). H2 `text-xl`, H3 `text-lg`.
- Rationale: once body is semibold, headings must separate by size, not weight.

### 3.5 Numerals

`tabular-nums` on every number that can be compared: table numeric columns, KPI values,
counters, durations, countdowns, pagination. Numeric columns are right-aligned.

---

## 4. Component Stylings

Primitives are shadcn/ui (`radix-nova`) in `web/src/components/ui`, with the overrides noted.
Global focus treatment:
`focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50`; base layer sets
`* { @apply border-border outline-ring/50 }`.
Invalid: `aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20`
(dark `/40`). Disabled: `opacity-50 pointer-events-none`.

### 4.1 Button

Base: `inline-flex items-center justify-center rounded-lg border border-transparent
bg-clip-padding text-sm font-medium whitespace-nowrap transition-all
active:not-aria-[haspopup]:translate-y-px`; icons default `size-4`.

| Variant | Recipe |
|---|---|
| `default` | `bg-primary text-primary-foreground hover:bg-primary/80` |
| `outline` | `border-border bg-background hover:bg-muted hover:text-foreground`; dark `border-input bg-input/30 hover:bg-input/50` |
| `secondary` | `bg-secondary text-secondary-foreground hover:bg-[color-mix(in_oklch,var(--secondary),var(--foreground)_5%)]` |
| `ghost` | `hover:bg-muted hover:text-foreground` (dark `hover:bg-muted/50`) |
| `destructive` | **soft**: `bg-destructive/10 text-destructive hover:bg-destructive/20` (dark `/20`→`/30`) |
| `link` | `text-primary underline-offset-4 hover:underline` |
| **ink** (`variant="ink"`) | `bg-foreground text-background hover:opacity-90 disabled:opacity-40`, on the normal size scale — commit actions: send / stop in the composer, Approve in the approval card, "Import N rows", "Import another file". The composer's send is icon-only (`icon`, or `icon-lg` on the empty-state composer) with `ArrowUp size-4 stroke-2`; stop = filled `Square size-4 fill-current`. |

| Size | Height | Padding / radius |
|---|---|---|
| `xs` | h-6 (24px) | `px-2 text-xs`, radius `min(var(--radius-md),10px)`, icon `size-3` |
| `sm` | h-7 (28px) | `px-2.5 text-[0.8rem]`, radius `min(var(--radius-md),12px)`, icon `size-3.5` |
| `default` | h-8 (32px) | `px-2.5 gap-1.5` |
| `lg` | h-9 (36px) | `px-2.5 gap-1.5` |
| `icon` / `icon-xs` / `icon-sm` / `icon-lg` | 32 / 24 / 28 / 36px square | |

Rule of thumb: **blue `default` for page-level CTAs** (today only Sign in); **ink for "send/commit"
in the composer, approvals and Import**; `outline` for secondary page actions and "Retry"; soft
`destructive` for dangerous actions.

### 4.2 Badge

`inline-flex h-5 w-fit items-center gap-1 rounded-4xl (pill) border border-transparent px-2
py-0.5 text-xs font-medium whitespace-nowrap`, icons `size-3`.
Variants: `default` (primary), `secondary`, `outline` (`border-border text-foreground`),
`ghost`, `link`, `neutral` (`bg-muted text-muted-foreground`), and soft semantic `success` /
`warning` / `info` / `destructive` (`bg-{tone}/10 text-{tone}`, dark `/20`). Status pills are
badges with `rounded-full` (and an optional `size-1.5` tone dot).

### 4.3 Inputs (input, select, composer textarea)

- `h-8 w-full rounded-lg border border-input bg-input-background px-2.5 py-1 text-base
  md:text-sm transition-colors placeholder:text-muted-foreground`; dark `bg-input/30`.
- Textarea: only the composer has one (§10.2), and it is not a bordered field: it is
  `data-slot="composer-input"` with `bg-transparent`, drawn on the composer card. It grows with
  its content up to 240px, then scrolls (script-sized, not `field-sizing`).
- Select: a native `<select>` wrapper (`NativeSelect`): `h-8` (`sm`: `h-7`), `rounded-lg border
  border-input bg-input-background pl-2.5 pr-8 text-base md:text-sm`, dark `bg-input/30`, with
  a non-interactive trailing `ChevronDown size-4 text-muted-foreground` at `right-2`.
- Menu item: `rounded-md px-1.5 py-1 text-sm gap-1.5 focus:bg-accent
  focus:text-accent-foreground`; destructive item `text-destructive focus:bg-destructive/10`.
- Fill: `Input` and `NativeSelect` each set `bg-input-background` themselves. The base layer in
  `globals.css` also fills `[data-slot="input"]`, `"textarea"` and `"select-trigger"`, but only
  `input` matches an element today: nothing carries the other two slots (the composer's
  textarea is `composer-input`, the select is `native-select`). A caller can still override the
  fill; the audit search field uses `bg-background` (4.11).
- File input: the base `Input` styles a native file button as `file:inline-flex file:h-6
  file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground`. No
  window shows one: the Import drop zone keeps its native input `sr-only` behind its
  "Choose file" button (10.9).

### 4.4 Card

No view uses the `Card` primitive: `components/ui/card.tsx` is imported nowhere. Every card on a
page writes its surface classes directly (the page-card recipe at the end of this list); the rest
is the primitive's recipe.

- `flex flex-col gap-4 overflow-hidden rounded-xl bg-card py-4 text-sm text-card-foreground
  ring-1 ring-foreground/10` with `--card-spacing: 1rem` (`size="sm"` → `0.75rem`).
- Header `px-(--card-spacing) gap-1`, with optional action slot top-right.
- Title `font-heading text-base leading-snug font-medium` (sm: `text-sm`).
- Description `text-sm text-muted-foreground`.
- Footer `border-t bg-muted/50 p-(--card-spacing) rounded-b-xl`.
- **Page cards** (list and table cards, filter panels, the audit overview cards, approval cards,
  empty and error states) add `shadow-sm`: `rounded-xl bg-card shadow-sm ring-1
  ring-foreground/10`, plus `overflow-hidden` for a table and the padding each one needs.

### 4.5 Table (primitive)

- Every table in `web/src` is the shared `DataTable` (`components/common/data-table.tsx`); the shadcn
  `Table` primitive in `components/ui` (recipe below) is not used by any view. `DataTable` cells are
  `border-b border-border/60 px-4 py-2.5` (none on the last row), heads add `sticky top-0 bg-card
  px-4 py-2.5`, rows `hover:bg-muted/50` with no selected state, the caption is `sr-only` and the
  optional footer is `border-t px-3 py-1.5 text-xs text-faint` (10.8).
- Primitive: container `relative w-full overflow-x-auto`; table `w-full caption-bottom text-sm`.
- Head cell `h-10 px-2 text-left align-middle text-caption font-medium tracking-wide
  whitespace-nowrap text-muted-foreground` — headers are deliberately **lighter** than cells.
- Cell `p-2 align-middle whitespace-nowrap`.
- Row `border-b transition-colors hover:bg-muted/50 data-[state=selected]:bg-muted`; last row
  no border. Header row `hover:bg-transparent`.
- Footer `border-t bg-muted/50 font-medium`; caption `mt-4 text-sm text-muted-foreground`.
- **Sortable head:** an `inline-flex items-center gap-1` button inside the `th`, `-mx-1 px-1 py-0.5 rounded-md
  text-caption font-medium tracking-wide`; icon `size-3.5` — unsorted `ChevronsUpDown
  opacity-30 group-hover:opacity-50`, sorted `ChevronUp/Down opacity-70`; label
  `text-foreground` when active else `text-muted-foreground`; `aria-sort` on the `th`;
  right-aligned numeric columns use `flex-row-reverse` (icon left of label).

### 4.6 Tabs

Tabs are used in one place: the category tabs of the Data page (`data-view.tsx`), in the `line`
variant. The default (pill) variant is the primitive's own and no view renders it.

- Both variants: trigger `text-sm font-medium text-foreground/60 hover:text-foreground`; the
  active trigger is `text-foreground`.
- `line` variant (the one in use): list `h-auto gap-5 border-b border-border bg-transparent`;
  trigger `rounded-sm px-0.5 pt-1 pb-2`; the active trigger shows a 2px `bg-foreground`
  underline (`after:h-0.5 after:-bottom-px`, opacity 0 → 100).
- Default (pill) variant, unused: list `inline-flex h-8 rounded-lg bg-muted p-[3px]
  text-muted-foreground`; trigger `rounded-md px-2 py-0.5`; active `bg-background
  text-foreground shadow-sm` (dark `border-input bg-input/30`).

### 4.7 Overlays

| Component | Recipe |
|---|---|
| Dialog overlay | `fixed inset-0 z-overlay bg-black/10 supports-backdrop-filter:backdrop-blur-xs` |
| Dialog content (alert dialog; the primitive exists, no view opens one yet) | `rounded-xl bg-popover p-4 text-sm ring-1 ring-foreground/10 gap-4 max-w-[calc(100%-2rem)] sm:max-w-sm`; **no shadow**; no close button (its footer holds the actions) |
| Dialog title / description | `font-heading text-base leading-snug font-semibold` / `text-sm leading-relaxed text-muted-foreground` |
| Dialog footer | `-mx-4 -mb-4 rounded-b-xl border-t bg-muted/50 p-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end` |
| Sheet (side panel) | same overlay (literal `z-50`); content `bg-popover text-sm shadow-lg`, primitive default `w-3/4 sm:max-w-sm`, entering with `duration-200 fade-in-0` and `slide-in-from-{side}-10`. The navigation sheet (from the left, below `md`) overrides it to `w-[min(22rem,100vw)] bg-sidebar p-0`; the audit entry opens from the right at `w-full sm:max-w-xl lg:max-w-2xl`, a Data record at `w-full sm:max-w-lg`. Title `font-heading text-base font-medium text-foreground` by default; the audit and record sheets set theirs in the serif `text-lg font-semibold tracking-tight`. Close button `absolute top-3 right-3`, ghost `icon-sm` |
| Popover (not built yet) | `w-72 rounded-lg bg-popover p-2.5 gap-2.5 text-sm shadow-md ring-1 ring-foreground/10` |
| Dropdown menu content | `min-w-32 rounded-lg bg-popover p-1 shadow-md ring-1 ring-foreground/10` (sub-menus `shadow-lg`); carries the `dark` class (shadcn `menuColor: "inverted"`), so menus use the dark tokens in both themes |
| Tooltip | **inverted**: `rounded-md bg-foreground px-3 py-1.5 text-xs text-background max-w-xs`, arrow `size-2.5 rounded-[2px]` |
| Toast (sonner, bottom-right; today only the session-ended notice) | `--normal-bg: var(--popover)`, `--normal-text: var(--popover-foreground)`, `--normal-border: var(--border)`, `--border-radius: var(--radius)`; lucide icons `CircleCheck`, `Info`, `TriangleAlert`, `OctagonX`, `Loader2 animate-spin` at `size-4` |

### 4.8 Loading & empty

- Skeleton `animate-pulse rounded-md bg-muted`; the Data table skeleton = `h-8` bar + rows of `h-10` in a list card.
- Loading wrapper: `role="status" aria-busy="true"` (the role implies `aria-live="polite"`; only the
  Approvals one spells it out) + sr-only label. The audit log shows a `role="status"` "Loading
  entries…" line in its table instead of a skeleton.
- Empty result (inside a card): `px-8 py-14 text-center text-sm text-muted-foreground`; line 1 `text-sm
  font-medium text-foreground`;
  when filters are active add `mt-1 text-caption text-faint` "N filters active" and an
  `outline sm` "Clear all filters" button (`mt-4`). Error variant uses `role="alert"`.
- Full-page empty card: `rounded-xl bg-card px-8 py-20 text-center text-sm
  text-muted-foreground shadow-sm ring-1 ring-foreground/10`.

### 4.9 Page header

- Wrapper `mb-6 flex items-start justify-between`.
- Not built yet: an optional back button, `outline` icon button `size-9 rounded-full mt-0.5`, `ArrowLeft size-4
  stroke-[1.75]` (prefers history back when there is in-app history; still a real link).
- Title `h1 font-serif text-2xl font-semibold leading-tight tracking-tight text-foreground`.
- Description `mt-1 text-nav leading-relaxed text-muted-foreground`.
- Caption `mt-0.5 text-xs leading-relaxed text-faint`.
- Action slot `ml-6 flex shrink-0 items-center gap-2` (right). The header does not set the document
  title: each route sets it with its own `generateMetadata`.

### 4.10 Stat tile (KPI)

Not used by any view yet (Appendix B). `rounded-lg bg-accent p-4 ring-1 ring-foreground/5`; label `text-xs text-muted-foreground`;
value `mt-1 text-2xl font-semibold tabular-nums` (`text-foreground`, or `text-destructive`
tone); hint `mt-0.5 text-xs text-muted-foreground`. Deliberately small — the value is below
page-title weight. Never use `faint` on this ground.

### 4.11 Filter bar

- Panel `mb-3 rounded-xl bg-card p-3 shadow-sm ring-1 ring-foreground/10`; row 1
  `flex flex-wrap items-center gap-2` (search + per-page filters).
- Search (audit log: user, target, request ID, grant ID; debounced, Enter commits at once): `relative min-w-40 flex-1 basis-full sm:max-w-56
  sm:basis-44`; input `h-8 bg-background pl-8 pr-8`; leading `Search size-4
  text-muted-foreground` at `left-2.5`; clear button `size-6 rounded-md` on the right. Not built
  yet: a suggestion list `absolute mt-1.5 w-full rounded-xl border bg-popover p-1.5 shadow-xl`,
  items `rounded-md px-3 py-2`, active `bg-accent text-accent-foreground`, primary line `text-sm
  font-medium tabular-nums`, secondary `text-xs text-muted-foreground`.
- Filter triggers (`NativeSelect`): **fixed width** (`w-40`, or `w-48` for the route source, on the
  audit log; the Data filter builder's field picker `w-48` and value control `w-44`) `h-8 text-sm` +
  truncate; active (audit selects only)
  `border-[var(--accent-foreground)]/35 bg-accent text-accent-foreground`.
- Active-filter chip row: `mt-2.5 flex flex-wrap gap-1.5 border-t border-border/60 pt-2.5`;
  leading label `text-caption text-faint` ("Filtered"); chip `inline-flex h-6 items-center
  gap-1 rounded-full border border-border bg-secondary pl-2.5 pr-1 text-xs` = dimension
  (`text-faint`) + value (`font-medium text-foreground`) + remove button `size-4 rounded-full
  text-faint hover:bg-foreground/10` with an enlarged hit area; trailing ghost "Clear all" (with two or more chips)
  `h-6 px-2 text-xs`.
- Not built yet: a result count line `mb-3 text-sm text-muted-foreground`: "**1,284** items" (number
  `font-medium tabular-nums text-foreground`) + optional `text-faint` "· filtered from N".
  Today the total appears **once** per screen, in the pagination bar's range.
- Pagination bar, the last row of the table card: `flex items-center justify-between border-t
  border-border px-3 py-2 text-sm text-muted-foreground`; the `tabular-nums` range "*from*–*to* of
  *total*" on the left, "Page *n* of *m*" (`text-caption tabular-nums text-faint`) and ghost
  `icon-sm` chevrons on the right.

### 4.12 Status primitives

- **Dot**: `size-1.5 rounded-full` (inline, trace, pills) / `size-2` (audit outcome list, request chain) in tone fill; `size-3` is for health cards (not built yet).
- **Pill**: a `Badge` (4.2) with `rounded-full` and the tone variant: `inline-flex h-5 rounded-full px-2 py-0.5 text-xs font-medium`.
- **Progress bar** (not built yet): track `h-1.5 w-full overflow-hidden rounded-full bg-foreground/10`, fill
  `h-full rounded-full bg-{tone}`.
- **Ring gauge** (not built yet): 96px SVG, track `stroke-foreground/10`, arc `stroke-{tone}`, center value
  `text-lg font-semibold tabular-nums text-{tone}` + `text-caption text-faint` sub-label.
- **Health card** (not built yet): `rounded-xl bg-card px-6 py-5 shadow-sm ring-1 ring-foreground/10`;
  failing state swaps to `ring-destructive/40`. Header row = dot + `text-base font-semibold`;
  detail row `mt-1.5 pl-6 flex flex-wrap gap-x-4 text-xs text-muted-foreground`.

### 4.13 Notice banner & text links

- Notice (inline, above content it qualifies): `rounded-lg bg-warning/10 px-3 py-2 text-xs
  text-warning ring-1 ring-warning/30`. Same shape in the `destructive` tone (a failed Data query, an upload problem); no view has an
  `info` notice yet. The stale-data notice adds an `outline xs` Retry.
- Quiet metadata note: plain `text-xs text-faint` (no container).
- Ghost text action ("Retry" after a transport error; text links such as "Open Approvals" and the
  audit links of a resolved approval card): `text-xs text-muted-foreground
  underline-offset-2 hover:text-foreground hover:underline`.
- Disclosure toggle (the trace summary "Thought for 3.4 s", the audit route table): native
  `<details>`; summary `inline-flex items-center gap-1 cursor-pointer list-none text-caption
  text-faint hover:text-muted-foreground` (`gap-2.5` in the trace summary, which has a mark
  column), with `ChevronDown size-3 stroke-2 transition-transform duration-150
  group-open:rotate-180`; hide the WebKit marker.

### 4.14 Icons

lucide, monochrome, inheriting `currentColor`. Sidebar/header icons `size-[18px]
stroke-[1.75]`; chat header action icons `size-5 stroke-[2.25]` (the mobile menu button's `Menu` is `size-5
stroke-[1.75]`); inline buttons `size-4`;
small chevrons `size-3 stroke-2`.

---

## 5. Layout Principles

### 5.1 Spacing

Tailwind 4 default scale (`--spacing: 0.25rem`), all rem so it follows the root knob.
Recurring values: `gap-1.5/2/2.5` inside controls and chips · `p-3` filter panel · `p-4` cards,
dialogs, KPI tiles · `px-6 py-5` health cards (not built) · `mb-3` under the filter panel and notices ·
`mb-6` under page header · `space-y-3` within a chat turn · `space-y-8` between turns ·
`space-y-5` between sidebar groups, `space-y-0.5` between items · `py-14`/`py-20` empty states.

### 5.2 Shell dimensions

| Element | Value |
|---|---|
| Sidebar | fixed, `w-60` (240px) expanded / `w-16` (64px) collapsed, `z-sidebar` (20); starts collapsed below `lg`; hidden below `md`, where a sheet replaces it |
| Top bar | `h-14 px-4 md:px-8` in the sticky header (`z-header`, 20) |
| Content offset | `md:pl-60` / `md:pl-16`, `transition-[padding] duration-200` |
| Content container | `mx-auto max-w-[1600px] px-4 pt-6 pb-8 md:px-8` |
| Chat column | `max-w-3xl` (48rem), centered |
| Wide chat turns | a turn with a table breaks out by `-mx-24` only when the Hub's full-width `@container/hub` wrapper is ≥ `62rem` (`@container/hub`, `@min-[62rem]/hub:-mx-24`) — the whole turn breaks out together |
| Split view | not built yet: two exact halves (`w-1/2` each, right pane `border-l`, no `gap`) |
| Dialog | `sm:max-w-sm` |

### 5.3 Grids

Main + aside (audit stats): `grid grid-cols-1 gap-4 xl:grid-cols-3` with main `xl:col-span-2`;
data view: entity picker rail + table `grid grid-cols-1 gap-6 lg:grid-cols-[14rem_minmax(0,1fr)]`.
Not built yet: KPI / status grids (`lg:grid-cols-2 xl:grid-cols-4`).

### 5.4 Z-index

Named tokens in `globals.css` (`--z-*`, with `z-*` utilities): `z-sidebar` 20 · `z-header` 20
(sticky top bar, docked composer) · `z-split` 30 (token only, no utility yet) · `z-panel` 40 (unused yet) ·
`z-overlay` 50 (dialogs, skip link). The shadcn menu, tooltip and sheet primitives keep a
literal `z-50`.

### 5.5 Whitespace philosophy

Dense but breathable: tables and lists keep tight row padding (`py-2.5` table cells, `py-1.5` list rows) while
section-level gaps are generous (`mb-6`, `space-y-8`). Width is constrained for reading
(chat `48rem`) and relaxed for data (`1600px`).

---

## 6. Depth & Elevation

### 6.1 Surface hierarchy

| Level | Surface | Recipe |
|---|---|---|
| 0 | Page | `bg-background`, no shadow |
| 0.5 | Sidebar | `bg-sidebar border-r border-sidebar-border` |
| 0.5 | Sticky header | `bg-background/90 backdrop-blur` |
| 1 | Card / panel | `bg-card ring-1 ring-foreground/10` (+ `shadow-sm` on every page card) |
| 1 | KPI tile (not built yet) | `bg-accent ring-1 ring-foreground/5` |
| 1 | In-chat data table | `rounded-xl border border-border bg-card` (no shadow) |
| 1 | Neutral reference block | `rounded-lg border border-border bg-muted/40 px-3 py-2` |
| 2 | Composer | `rounded-2xl bg-card shadow-lg shadow-foreground/5 ring-1 ring-foreground/10` |
| 3 | Menu (popover not built; selects are native) | `bg-popover shadow-md ring-1 ring-foreground/10 rounded-lg` |
| 3 | Search suggestions (not built yet) | `bg-popover border shadow-xl rounded-xl` |
| 4 | Dialog (primitive only) | `bg-popover ring-1 ring-foreground/10 rounded-xl`, **no shadow**, over `bg-black/10 backdrop-blur-xs` |
| 4 | Sheet (side panel) | `bg-popover shadow-lg` (navigation sheet `bg-sidebar`) |

Tailwind 4 shadow values: `shadow-sm` = `0 1px 3px 0 rgb(0 0 0/.1), 0 1px 2px -1px rgb(0 0 0/.1)` ·
`shadow-md` = `0 4px 6px -1px rgb(0 0 0/.1), 0 2px 4px -2px rgb(0 0 0/.1)` ·
`shadow-lg` = `0 10px 15px -3px …, 0 4px 6px -4px …` · `shadow-xl` = `0 20px 25px -5px …,
0 8px 10px -6px …` · `shadow-2xl` = `0 25px 50px -12px rgb(0 0 0/.25)`.

### 6.2 Radius scale

Derived from `--radius: 0.75rem`:

| Token | Formula | px | Typical use |
|---|---|---|---|
| `rounded-sm` | ×0.6 | 7.2 | Small tags |
| `rounded-md` | ×0.8 | 9.6 | Sidebar items, menu items, tooltips, skeletons |
| `rounded-lg` | ×1.0 | 12 | Buttons, inputs, menus, notices |
| `rounded-xl` | ×1.4 | 16.8 | Cards, dialogs, tables, user bubble |
| `rounded-2xl` | ×1.8 | 21.6 | Composer, import drop zone |
| `rounded-3xl` | ×2.2 | 26.4 | — |
| `rounded-4xl` | ×2.6 | 31.2 | Badges (pill) |
| `rounded-full` | — | — | Avatars, chips, clarify chips, dots, status pills |

### 6.3 Motion

| Token / class | Spec |
|---|---|
| `--ease-standard` | `cubic-bezier(0.4, 0, 0.2, 1)` |
| `.ai-enter` | 200ms, `opacity 0 → 1`, `translateY(2px) → 0` |
| `.ai-exit` | 150ms, `opacity → 0`, `scale(0.98)`. Defined in `globals.css` but not used yet: nothing animates out |
| Turn entry | `animate-in fade-in duration-300` |
| Thinking dots `.ai-dots` | three 4px dots, gap 3px, `currentColor`; 1.2s loop, stagger 0.15s; keyframe rest `opacity .25 scale(.85)`, peak at 30% `opacity 1 translateY(-3px) scale(1)` — reads as one wave |
| Typewriter | 25ms per character, only on the active trace line; restarts when the line text changes; the whole line at once under reduced motion |
| Trace line aging | `transition-colors 240ms var(--ease-standard)` |
| Overlays | menus and the dialog `duration-100`, `fade-in-0 zoom-in-95`; menus and tooltips also `slide-in-from-{side}-2`; sheets `duration-200`, `fade-in-0`, `slide-in-from-{side}-10` |
| Disclosure chevron | `rotate-180`, 150ms |
| Sidebar collapse | width/padding 200ms |
| Button press | `translate-y-px` |
| Route transitions | None: navigation is instant (the reduced-motion clamp below still covers `::view-transition-*`) |

Rules: keyframe animations move only `transform` and `opacity`; transitions also change colours
(hover, trace aging, chart fills) and the sidebar's width and the content's padding. Global `prefers-reduced-motion: reduce` clamps
all animations/transitions (including `::view-transition-*`) to `0.01ms`, single iteration.
Resting keyframes are chosen so a clamped animation still leaves elements visible.

### 6.4 AI activity glow

`.ai-glow` on the composer; `::before` at `inset: -4px; z-index: -1; border-radius: inherit;
background: var(--ai-glow-grad); filter: blur(24px); opacity: 0; will-change: opacity`.
With `[data-active]`: fades to `opacity .15` over 200ms, then breathes `.15 ↔ .22` on a 2.6s
`--ease-standard` loop (first/last keyframe must equal .15). The host must **not** create
a stacking context (no `transform`, `filter`, `isolation`, `z-index`, `will-change`), or the glow
paints over the card's own fill. The glow sits in the nearest stacking context above the host, under
any opaque block between them: the docked composer's sticky `z-header` wrapper is that context, so
the glow shows over its gradient, around the card. The empty-state composer is never busy (the first
question docks the composer), so it never glows.

---

## 7. Do's and Don'ts

**Do**
- Keep greys true-neutral (R = G = B) in both themes.
- Let color mean something: blue = interactive/primary/info; green/amber/red = state only.
- Use hairline rings (`ring-foreground/10`) for surface edges.
- Use soft state fills via opacity (`bg-{tone}/10`, dark `/20`) instead of new tokens.
- Keep the sidebar's active item a **neutral** grey pill, not blue.
- Use `tabular-nums` and right alignment for every comparable number.
- Keep table headers at caption tier (lighter than cells).
- Size type in rem; register named tokens for any non-standard size.
- Keep the answer first: the reasoning trace collapses to one summary row, and provenance is
  one quiet `faint` line (or the result table's header strip), shown once.
- Say freshness honestly: show the real `as of` time, or "as-of unknown" — never the query time.
- Show a total count once per screen.
- Make only the latest turn's chips/buttons interactive; historical turns render static.
- Use fixed-width filter triggers with truncation so the toolbar never jumps.
- Re-verify AA contrast on every ground when touching a text tier.
- Respect `prefers-reduced-motion` globally.

**Don't**
- Don't add warm/cool tints to neutrals, or a second brand hue.
- Don't use colored spinners, gradient orbs or blue dots as thinking indicators.
- Don't put chat answers inside cards.
- Don't use the serif for body, tables, buttons or numbers.
- Don't use raw Tailwind palette classes (`amber-100`, `sky-700`, `emerald-950`…) for
  statuses — map them to the semantic tokens (`web/src` has none).
- Don't show `0 ms` for steps without real timing (false precision) — omit the duration.
- Don't show the same trace or the same total twice in one view.
- Don't give dialogs drop shadows; don't stack a ring and a left border (double outline).
- Don't put `faint` text on accent grounds.
- Don't make KPI numbers louder than the page title.
- Don't hard-code `px` font sizes.

---

## 8. Responsive Behavior

- **Desktop-first.** Primary target is a 1280–1600px work display; the console is usable down
  to tablet width.
- Breakpoints: Tailwind defaults `sm 40rem` · `md 48rem` · `lg 64rem` · `xl 80rem` ·
  `2xl 96rem`. Layout decisions inside the chat column use **container queries**
  (`@container/hub` + `@min-[62rem]/hub:`), not viewport breakpoints, because the column's
  width depends on the sidebar state (240px or 64px), not only on the viewport.
- Inputs are `text-base` below `md` and `text-sm` from `md` (avoids iOS zoom on focus).
  **Known deviation:** an audit filter that is mono (target, request ID, grant ID) is
  `font-mono text-xs md:text-xs` once it holds text (`audit-filters.tsx`), so a filled one is
  `text-xs` at every width, including below `md`, and the zoom guard does not hold for it. An
  empty one is `text-base` below `md` like any other input.
- Dialog footers stack (`flex-col-reverse`) below `sm`, row-aligned right from `sm` (primitive only; no view opens a dialog yet).
- Sidebar: a fixed rail from `md` (64px below `lg`, 240px from `lg` unless the user toggles it);
  below `md` it is hidden and a left sheet, opened from the top bar's menu button, holds the
  same navigation. The audit entry and a Data record open in a right sheet, full width below `sm`.
- Filter search `basis-full` on narrow screens, `sm:basis-44`.
- Grids collapse to one column below `lg` (data view) or `xl` (audit stats).
- Touch targets: control heights are 24–40px (desktop density). On coarse pointers
  (`@media (pointer: coarse)`), elements marked `data-touch-target` get `min-height: 44px`:
  sidebar items, clarify chips, hub starter chips, segmented-control options, the data entity
  picker and the audit outcome filter rows. Mark any new chip or row action the same way.
- A larger root font size (the knob's 14–20px range, §3.2) must not break layouts: verify key
  screens at 20px.

---

## 9. Agent Prompt Guide

### 9.1 Quick color reference

- Ground `#fafafa` / dark `#1a1a1a` · Card `#ffffff` / `#242424` · Ink `#1a1a1a` / `#ebebeb`
- Accent blue `#2f6bed` / `#5b8cf5` · Blue tint `#e8effc` / `#23324e` · Text on tint `#2a5fd4` / `#9db8f5`
- Text tiers: `#1a1a1a` → `#5c5c5c` → `#6a6a6a` (dark `#ebebeb` → `#a3a3a3` → `#999999`)
- Border `#e5e5e5` / `#333333` · Success `#15803d` · Warning `#b45309` · Destructive `#dc2626`

### 9.2 Ready-to-use prompts

- **Primary button:** "shadcn Button default: `h-8 rounded-lg px-2.5 text-sm font-medium
  bg-primary text-primary-foreground hover:bg-primary/80`, focus `ring-3 ring-ring/50`,
  `active:translate-y-px`."
- **Ink send button:** "shadcn Button `variant="ink"` (`bg-foreground text-background
  hover:opacity-90 disabled:opacity-40`), `size="icon"`, with lucide `ArrowUp size-4 stroke-2`."
- **List card with table:** "`overflow-hidden rounded-xl bg-card shadow-sm ring-1
  ring-foreground/10`; headers `text-caption font-medium tracking-wide text-muted-foreground`;
  cells `border-b border-border/60 px-4 py-2.5`, rows `hover:bg-muted/50`; numeric cells right-aligned `tabular-nums`."
- **KPI tile (not built yet):** "`rounded-lg bg-accent p-4 ring-1 ring-foreground/5`, label `text-xs
  text-muted-foreground`, value `mt-1 text-2xl font-semibold tabular-nums`."
- **Page header:** "serif `text-2xl font-semibold tracking-tight` title, `text-nav
  text-muted-foreground` description, actions right, `mb-6`."
- **Status pill:** "`rounded-full px-2 py-0.5 text-xs font-medium bg-success/10
  text-success` (warning / destructive / muted equivalents)."
- **Warning notice:** "`rounded-lg bg-warning/10 px-3 py-2 text-xs text-warning ring-1
  ring-warning/30`."
- **Dialog:** "`rounded-xl bg-popover p-4 ring-1 ring-foreground/10`, no shadow, overlay
  `bg-black/10 backdrop-blur-xs`, footer `border-t bg-muted/50`."

---

## 10. Console Patterns (twotrack)

### 10.1 App shell + sidebar

- **Sidebar** (`aside`, fixed left, `bg-sidebar border-r border-sidebar-border`, `z-sidebar`,
  `transition-[width] duration-200`; 240px expanded, 64px collapsed; hidden below `md`):
  - Top row `py-4 flex items-center` (expanded `gap-2.5 px-4`; collapsed `justify-center
    px-2`): product mark (`size-8`, monochrome `text-foreground`: two parallel rounded bars,
    one solid and one at 40% opacity) + wordmark `font-serif text-lg font-bold tracking-tight`,
    together a link to Hub; then a `PanelLeft` collapse button `ml-auto size-8 rounded-md
    text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground`. Collapsed: only
    the mark, centered in a `size-10` button that expands the rail.
  - Nav `flex-1 overflow-y-auto py-4 px-3 space-y-5` (collapsed `px-2 space-y-1`). Group label
    `mb-1.5 px-3 text-nav font-medium text-muted-foreground` (hidden when collapsed). Items
    `flex items-center gap-3.5 rounded-md px-3 py-2.5 text-nav` with icon `size-[18px]
    stroke-[1.75]`; inactive `text-foreground/80 hover:bg-sidebar-accent/60
    hover:text-foreground`; active `bg-sidebar-accent font-semibold
    text-sidebar-accent-foreground` (`aria-current="page"`). Collapsed items are `size-10`
    centered icons with `title` and an sr-only label.
  - Groups: *Agent* (Hub, Approvals) · *Data* (Data, Import) · *Governance* (Audit). Import and
    Audit are for the `operator` role (`admin` passes every check); gated items are hidden, not
    disabled.
  - Pending approvals: the Approvals item shows the count as a pill `ml-auto h-5 min-w-5
    rounded-full bg-warning/10 px-1.5 text-xs font-medium text-warning tabular-nums` (dark
    `/20`); collapsed, as a `size-2 rounded-full bg-warning ring-2 ring-sidebar` dot at the
    icon's top right. Screen readers get "N pending approvals".
- **Collapsing**: the rail starts collapsed below `lg` and expanded from `lg`; the toggle
  overrides that until the page reloads. Below `md` the sidebar is hidden, and the menu button
  in the top bar opens a left sheet (4.7) with the same nav; the sheet closes on navigation.
- **Top bar** (in the sticky header, `sticky top-0 z-header`; the demo banner sits above it):
  `h-14 flex items-center gap-3 bg-background/90 px-4 backdrop-blur md:px-8`. Order: menu
  button (below `md`) · page label (Hub only: `text-page-label font-semibold tracking-wide`) ·
  right cluster `ml-auto flex items-center gap-1`: model indicator, new-conversation button
  (Hub, once there is a turn), locale toggle, theme toggle, divider `mx-1 h-5 w-px bg-border`,
  user menu.
  - New conversation: ghost icon button `size-10 rounded-lg hover:bg-accent`, `SquarePen size-5
    stroke-[2.25]`, inverted tooltip.
  - Locale toggle: ghost `sm` button `min-w-10 px-2 text-sm font-medium
    text-muted-foreground` showing the other language ("中文" / "EN"); it writes the
    `NEXT_LOCALE` cookie and refreshes (no locale in the URL).
  - Theme toggle: ghost icon button (`size-[18px] stroke-[1.75]`; the icon is the resolved
    theme, Sun or Moon) that opens radio items Light / Dark / System. `next-themes` with
    `attribute="class"` (the `dark` custom variant is `&:is(.dark *)`), default `system`,
    `disableTransitionOnChange`. Verify every pattern below in both themes.
  - User menu: ghost button with avatar (`size-8 rounded-full bg-muted text-xs font-semibold
    text-muted-foreground`, initial of the user id), user id (`text-sm font-semibold`,
    `max-w-40 truncate`) over role (`text-caption text-muted-foreground`); id and role are
    hidden below `sm`. The menu shows "Signed in as …", the role, and **Sign out**.
- **Demo banner** (only while the server runs in demo mode: passwordless sign-in, synthetic
  data): `role="note"` strip `border-b border-warning/30 px-4 py-1.5 text-center text-xs
  font-medium text-warning` on an opaque warning tint (10% light, 20% dark, of `--warning` over
  `--background`), `TriangleAlert size-3.5 stroke-2` + one sentence.
- **Content**: `min-h-svh bg-background`, offset `md:pl-60` / `md:pl-16`; `main#main` is `mx-auto
  flex w-full max-w-[1600px] flex-1 flex-col px-4 pt-6 pb-8 md:px-8`. A "Skip to content" link
  is the first focusable element (`sr-only`, visible on focus).
- **Auth/loading guard**: centered `text-sm text-muted-foreground` message; on connection
  failure keep the session and show an `outline sm` "Retry" button.
- **Model indicator** (top bar, and the sign-in page's top row) — only while `/api/health` says
  `provider.mock` (no model API key: routing by rules and keyword search, template answers): a
  neutral pill `h-6 rounded-full bg-muted px-2.5 text-xs font-medium text-muted-foreground` with
  `BotOff size-3.5 stroke-[1.75]` and "No model" / 「未配置模型」, first in the top bar's right
  cluster (`mr-1`). It is a focusable `button` (no action) so the inverted tooltip opens on
  hover and focus; the same sentence is its `aria-describedby` for screen readers. Nothing is
  shown when a model is configured or before health answers.
- **Sign-out** clears the session at once and asks `POST /api/auth/logout` to revoke the token on
  the side; its answer (a 401 for a token already dead, a network error) never holds the user.

### 10.2 Chat surface

- The page label and the new-conversation action live in the shell's top bar (10.1); the page
  itself is the empty state or the transcript with a docked composer.
- **Empty state**: centered column (`flex flex-1 flex-col items-center justify-center pt-10
  pb-24`), serif greeting `text-4xl font-medium tracking-tight mb-10` ("What do you want to
  know?"), then the composer at `max-w-3xl rounded-2xl bg-card p-4 shadow-sm ring-1
  ring-foreground/10`, starter queries (`mt-5`, centered: label `text-caption text-faint`, then
  `gap-2` chips `rounded-full border border-border bg-card px-3.5 py-1.5 text-sm
  text-muted-foreground hover:bg-accent hover:text-accent-foreground`), and the footnote.
- **Transcript** (in a full-width `@container/hub` wrapper): `mx-auto w-full max-w-3xl flex-1 pt-2 pb-6`, turns
  `space-y-8`, container `role="log" aria-live="polite"`.
- **User bubble**: right-aligned, `max-w-[85%] rounded-xl rounded-br-sm bg-secondary px-3.5
  py-2 text-answer font-medium leading-relaxed text-secondary-foreground` — same size as the
  answer, one weight lighter, so the question never outweighs the answer. A turn re-run from a
  clarify choice adds `mt-1 block text-xs font-normal text-muted-foreground` "Run as *tool_id*" (the chosen report's id).
- **Composer** (docked at the bottom): full-width wrapper `sticky bottom-0 z-header w-full
  bg-linear-to-t from-background via-background/95 to-transparent pt-6 pb-1` (wide tables
  scroll under it, not over it), inner `mx-auto w-full max-w-3xl`; card `ai-glow rounded-2xl
  bg-card px-4 py-3 shadow-lg shadow-foreground/5 ring-1 ring-foreground/10` (`data-active`
  while the agent runs); textarea `bg-transparent px-1 pb-3 pt-1 text-answer outline-none
  placeholder:text-faint`; bottom row `flex items-center justify-end gap-2` with the ink send
  button, which becomes Stop while a query runs. Enter sends, Shift+Enter adds a line, an IME
  composition never sends, and typing the next question stays possible while one runs. Footnote
  below `pt-2 pb-2 text-center text-caption text-faint`: "Numbers come only from deterministic
  tools; the language model never sees your rows."; while busy: "Working on your question.
  Press Stop to cancel."
- Not built yet: the attached-context chips row (`mb-2.5 flex flex-wrap gap-1.5`) and tools
  left of send; a side panel and task list in the top bar; a chat drag-and-drop overlay
  (`absolute inset-0 z-50 rounded-2xl bg-background/80`, 2px dashed `primary` outline, centered
  `text-sm font-medium` hint; the Import page has its own drop zone, 10.9); a split view
  (right pane `w-1/2 border-l`).

### 10.3 Collapsible reasoning trace

Data contract: the backend's ordered steps (`step` is an open string; this build knows
`candidates`, `understand`, `handover`, `scope`, `gate`, `execute`, `narrate`, `respond`), each
with `status` (`ok | blocked | skipped | error`), `source` (the routing arm: `explicit | rule |
llm | llm_confirm | semantic | agent | mcp | fallback`), `facts` and `duration_ms`. Display only
backend facts; never fabricate "thoughts". Every line is built from those fields in
`web/src/lib/trace.ts` and worded in `messages/*.json` under `trace.step`. A step this build
does not know reads "*step* (*status*)" with its first plain facts as a `text-faint` note.

- **Mark column**: every line starts with a fixed `w-[18px] h-[21px]` column (= width of the
  three-dot indicator) so text left edges never shift.
- **Step dot** `size-1.5 rounded-full`, by the line's tone: `ok` → `bg-success`; `empty` (zero
  rows — a result, not an error) → `bg-current opacity-70`; `warn` (blocked, a write held for
  approval, a failed model call, numeric-lock removals) → `bg-warning`; `error` →
  `bg-destructive`; `neutral` (skipped steps, "waiting for your choice", a run you chose to
  leave unfiltered, egress redactions) → `bg-current opacity-40`. Only the dot is tinted: the
  sentence keeps the list's text tier.
- **Line**: `flex items-start gap-2.5 text-caption font-normal leading-relaxed` = dot · text
  (`min-w-0 flex-1`, plus the server's own reason as a `text-faint` " · note") · route-source tag
  · duration `shrink-0 tabular-nums text-faint` (`< 1000` → `"412 ms"`, `< 10 s` → `"3.4 s"`, else
  `"12 s"`; omit when `duration_ms = 0`).
- **Route-source tag**: `inline-flex h-5 rounded-sm bg-muted px-1.5 font-mono text-xs
  text-muted-foreground`, value verbatim (it is what the audit log records); its meaning
  ("Keyword rule arm", "Chat fallback: no report matched", …) is the `title` and sr-only text.
- **Line wording** (English from `messages/en.json`; *Tool* is the target's id as the step's
  facts record it, not "Label (id)"):
  - Route (`understand`): `rule` "Matched by keywords → *Tool*" · `explicit` "Ran *Tool* as
    selected" · `llm` "Asked the language model → *Tool*" · `llm_confirm` "Language model
    suggested *Tool*; needs your confirmation" · `semantic` "Semantic match → *Tool*" · an open
    task or `agent` "No single report fits; planning with the agent loop" · a chat turn, whatever
    its source (including `fallback`) "No report matches; answering as general chat" · one
    candidate to confirm "*Tool* needs your confirmation before it runs" · several "N candidate
    reports; asking you to choose" (`bg-warning`) · any other source "Routed to *Tool*".
  - Hand-over (`handover`, no fixed report covers the question): "Handed over to exploration: no
    report covers the question", with "(closest semantic match *Tool*: *score*, threshold
    *threshold*)" when the evidence names one; another reason reads "Handed over to exploration
    (*reason*)".
  - Scope (`scope`): "Scope check: …" — the items you named are bound to the parameters, not
    covered by them, conflict with them, or ran unfiltered as you chose; or "no specific item
    named".
  - Gate (`gate`): "Permission and precondition check passed" · "Permission check passed; your
    approval was used" · "Write held until you approve it" · "Waiting for your choice" ·
    "Blocked: …" (your role, a precondition, the parameters, an unknown report, an invalid /
    expired / used approval; `bg-warning`).
  - Execute (`execute`): "Ran deterministic tool (12 rows)" (`empty` dot at 0 rows) · "Workflow
    *name*: step *step* (*kind*)" · "Needs more input before it can run" · "Stopped: the input
    was rejected (*error*)" · "Tool failed (*error*)" · "Tool failed and was rolled back
    (*error*)".
  - **Exploration** (`execute` steps with a `loop` fact, when the agent loop handles a question
    no fixed report covers): start "Exploration started (built-in loop | Pi | *runtime*) with no
    tools | 1 tool: *a* | 2 tools: *a*, *b*" (every tool it may call) · turn "Agent turn 2:
    requested *a*, *b*" · answer "Agent answered on turn 2" · stop "Agent stopped: *reason*"
    (the stop reason with `_` read as a space, e.g. "max turns"; its labelled form is the
    stop-reason line, 10.4) · error "Agent turn 2 failed (*error*)" (`bg-destructive`) ·
    refusal "Exploration refused: the request names a specific report | the request already
    ended on its fixed path (denied | invalid input | blocked | an error | a spent budget |
    waiting for approval | answered | ran) | the request was never handed over".
  - Narrate (`narrate`): one answer-source line (next table), then the numeric lock: "Numeric
    lock: every number checked against the result" (`ok`; only for model-written text with
    nothing removed) or "Numeric lock removed 2 sentences" (`bg-warning`; whenever any were).
  - Respond (`respond`): "Answer assembled (12 rows)" · one "Model call failed: routing |
    narration | chat | agent step (*error*, HTTP *status*)" per failed model call
    (`bg-warning`) · "Egress guard redacted 2 values before the model call" · "Finished with an
    error" (`bg-destructive`). A failed call is named by its call, error class and HTTP status
    only (`, HTTP *status*` is dropped when there is none); the provider's message is never
    shown.

**Answer-source lines** — the `narrate` line says who wrote the answer, in one of three states
that a reader can tell apart. `narration()` in `trace.ts` checks them in this order:

| State | English / Chinese | Dot | When |
|---|---|---|---|
| Mock | "Template narration (no model configured)" / 「模板叙述（未配置模型）」 | `bg-success` | `facts.mock` is `true`: the zero-key mock provider answers on the model's path with template text; this wins over `source` |
| Failed | "Model call failed (*error*, HTTP *status*); narrated from a fixed template" / 「模型调用失败（…），改由固定模板叙述」 | `bg-warning` (amber) | the narrate facts carry `model_error` and the text is not model-written (`facts.source` ≠ `model`) |
| Model | "Narrated by the language model" / 「由语言模型叙述」 | `bg-success` | `facts.source` is `model` |

Template text with no mock and no failed call behind it reads "Narrated from a fixed template"
/ 「由固定模板叙述」 (`bg-success`); `trace.ts` names the numeric lock removing every sentence the
model wrote as that case. Only the dot carries the amber of the Failed state; a failed model
call also shows as the amber model-failure note under the answer (10.4).

- **Live state**: while a request streams, each backend step appears as a line when it arrives,
  and completed lines age by lightness — newest `text-muted-foreground`, next two `text-faint`,
  older `text-faint opacity-60`. At most the newest 5 completed lines show; older ones collapse
  into "…N earlier steps" (`text-caption text-faint`, indented past the mark column). The final
  line is the only `text-foreground` line: the three-dot wave in the mark column + a typewriter
  label taken from the last step received ("Understanding the question", "Running *Tool*",
  "Preparing the options", "Planning with the available tools", "Writing the answer", "Finishing
  up"). Lines enter with `.ai-enter`. After 20 s a quiet `text-caption text-faint` patience hint
  follows ("Some tools take a while. The server is still working.").
- **Done**: the list collapses in place into a `<details>` summary row: mark-column dot
  (`bg-current opacity-70`) + a `tabular-nums` summary + `ChevronDown size-3` (`text-caption
  text-faint hover:text-muted-foreground`). The summary is the client-measured wall time:
  "Thought for 12 s"; "Stopped after 4.1 s" (cancelled); "Failed after 0.4 s"; "Ran after
  approval in 1.2 s" (the trace of an approved write, inside its approval card); or "Reasoning
  trace (N steps)" when no time was measured. Expanded = the full step list, all lines
  `text-muted-foreground`, each with its route tag and duration. The summary row renders for
  every route, since route attribution is an audit requirement. The audit entry's "Trace replay"
  opens the same list.
- Show the trace in exactly one place per turn.

### 10.4 Answer, provenance and numeric-lock notice

Block order inside a turn (`TurnView`, `space-y-3`): user bubble · reasoning trace (10.3) · a
failure the server reported · **answer** · **stop-reason line** · numeric-lock notice ·
model-failure note · clarify chips (10.5) · approval card (10.6) · result table with its
provenance strip (10.8), or the provenance line alone when the answer has no table · last, a
transport-error line or the cancelled note.

- **Answer**: `.report-prose.answer-prose` directly on the page background (no card), entering
  with `.ai-enter`. Links render as plain text showing the URL, whoever wrote the answer (model,
  template or system); nothing in an answer is clickable
  (`web/src/components/common/markdown.tsx`, `markdown.test.tsx`). A pending write's system
  text is not repeated above its approval card, which says it once.
- **Qualifying notes** (anything that changes how the answer should be read: the stop reason, the
  numeric lock, a failed model call) are one-line `text-xs` notes directly under the answer, not
  banners; the warning notice banner (4.13) is for page data that may be stale.
- **Stop-reason line** (an exploration run that did not finish): component `StopReasonLine` in
  `web/src/components/hub/turn-view.tsx`, fed by `exploreStop()` in `web/src/lib/trace.ts`,
  which reads the one `execute` step that carries `stop_reason`. One line, `text-xs`, directly
  under the answer and above the numeric-lock notice, with `data-stop-reason="<reason>"`: the
  label "Stop reason:" (`hub.stopLabel`, `font-medium`) then the reason (`hub.stopReason`).
  - Tone: `text-warning` for a spent budget (`max_turns`, `max_tool_calls`); `text-destructive`
    for any other stop, a refused exploration (`handover_refused`) and failures included.
  - Nothing is shown for a run that answered (`answered`) or waits for an approval
    (`needs_confirmation`), nor for a turn that did not explore.
  - Wording: `max_turns` "turn limit reached" · `max_tool_calls` "tool-call limit reached" ·
    `handover_refused` "exploration was refused" · `timeout` "out of time" · `worker_crashed`
    "the worker crashed" · `protocol_error` "the worker broke protocol" · `worker_unavailable`
    "the worker was unavailable", plus its detail: `startup_timeout` " (it did not start in
    time)", `version_drift` " (its version is not the pinned one)", `launch_failed` " (it could
    not be launched)" · `model_error` "the model call failed" · `audit_failed` "a step could not
    be written to the audit log" · `runtime_error` "the runtime failed". Any other reason prints
    verbatim; a reason or detail that is not a plain identifier reads "?".
  - The answer text already leads with the stop; this line is its label.
- **Numeric-lock notice** (below the stop-reason line, only when `narrative_stripped > 0`):
  `text-xs text-faint`, e.g. "Numeric lock removed 2 sentences with unverified numbers. Table
  values are unaffected."
- **Model-failure note** (only when a model call failed while answering): `text-xs
  text-warning`, naming each failed call once ("routing", "narration", "chat", "agent step") and
  what stood in for it — "Model call failed (narration); this answer was narrated from a fixed
  template." or "Model call failed (routing); routing used rules and keyword search." (plural
  "Model calls failed (routing, narration); …"). It names calls only, never the provider's
  message. The zero-key mock sends no failure, so it shows no note.
- **Provenance line**: `text-xs text-faint`, single line:
  `Source: Inventory by SKU (inventory_by_sku) · 128 rows · as of 2026-10-26 08:00 · scope: warehouse=WH-02`
  — label then tool id in parentheses, row count, real data as-of (or "as-of unknown"), scope.
  When a table is shown, the table header strip carries label + meta instead, so the line is
  not repeated on screen.
- **"Label (tool_id)" everywhere a tool is named** (provenance, clarify chips, approval titles):
  the server's label (worded in the reader's language) first, then the id the audit log
  records in `font-mono text-xs font-normal text-faint` — `opacity-70` and the parent's colour
  instead where the ground inverts (a hovered chip). Parentheses come from the messages
  (`common.labelWithId`): `(id)` after a space in English, full-width `（id）` with no space in
  Chinese. No label, or a label equal to the id: the id alone, in mono.
- **Error** (`role="alert"`, `text-nav leading-relaxed text-destructive`): a failure the server
  reported ("The request failed: *message*") sits above the answer and is a verdict, with no
  Retry. A transport failure (no network, a timeout, a stream that ended early) sits last and
  adds ghost text "Retry", only on the latest turn and only while nothing else is running. A
  cancelled turn ends with `text-xs text-faint` "Stopped before the answer arrived."

### 10.5 Clarify candidate chips

- The question is the answer text above (10.4); chips sit below it in `div[role=group]
  flex flex-wrap gap-2`, followed, while they are interactive, by the hint `text-caption text-faint`
  "Pick one to run it as the selected report."
- Chip: `rounded-full border border-primary/30 bg-accent px-3.5 py-1.5 text-sm font-medium
  text-accent-foreground transition-colors hover:bg-primary hover:text-primary-foreground
  disabled:opacity-40`. The scope guard's choices use the same chips.
- Interactive only on the latest turn; disabled while a request is in flight.
- Chip text is the option's label, then its report id ("Show all categories (low_stock)",
  per 10.4), then any tag: "Unfiltered" (`rounded-sm bg-muted px-1.5 text-xs`, with a tooltip:
  "Runs without the filter you asked for. The answer says it is unfiltered.") or "· writes" for
  a chip that would write (`text-xs font-normal opacity-80`).
- Not built yet — multi-question variant (option list): rows `flex w-full items-start gap-2.5 rounded-md px-2
  py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground`, selected `bg-accent
  font-medium text-accent-foreground`; leading index `w-3 text-xs tabular-nums
  text-muted-foreground`; recommended tag `rounded bg-primary/15 px-1.5 py-0.5 text-xs
  text-primary`; option description `block text-xs text-muted-foreground`; pager `1/3` with
  `icon-xs` ghost chevrons.

### 10.6 Approval card with countdown

Composed from the card, notice and status recipes above; one card (`ApprovalCard`) serves the
Hub, the Approvals window and the Import wizard.

- Container `rounded-xl bg-card p-4 shadow-sm ring-1` in the transcript: `ring-warning/40`
  while pending and the viewer may approve it, otherwise `ring-foreground/10`.
- Header row `flex flex-wrap items-start gap-2`: title `min-w-0 flex-1 text-sm font-medium
  text-foreground` ("Approval required · Create purchase order (create_po)"; on the Approvals
  window "Write request · Create purchase order (create_po)", and an import's own label "Import
  Packaging (packaging)"), on the Approvals window an `info` pill "Requested by *user*" (another user's
  request) and the route-source tag, then a `warning` pill
  "Write".
- Why line `mt-1 text-xs leading-relaxed text-muted-foreground`: "This request changes data, so
  it runs only after you approve it."
- Body: key–value summary as `dl mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1`, `dt
  text-caption text-muted-foreground` ("Request", "Grant"), `dd text-sm tabular-nums
  text-foreground` (the grant id in `font-mono text-xs text-faint`). Not built yet: diffs or
  affected rows in the in-chat data table.
- A failure sits below the body as `mt-3 text-xs leading-relaxed text-destructive`
  (`role="alert"`).
- Footer `mt-3 flex flex-wrap items-center gap-3`: countdown left `text-caption tabular-nums
  text-faint` ("Expires in 0:42"), switching to `text-warning` in the last 20%; bar `h-1 w-16
  rounded-full bg-foreground/10` with fill `bg-foreground/40` → `bg-warning`; then an "Open
  Approvals" link `text-xs text-muted-foreground underline-offset-2` and any hint. Actions
  right `ml-auto flex gap-3`: Reject (`outline sm`), Approve (ink `sm`; shown only to the
  requester, others get "Only the requester can approve. You can reject."). A card that is not
  the latest turn's is static, with "Only the latest pending confirmation can be approved
  here."
- Countdown math: from the server's `expires_in_s` (time left on its own clock) as of receipt,
  on the page's monotonic clock, floored at 0 (on the Approvals window also capped at the server's
  own window, `expires_at − created_at`), ticking every 1s — the browser's wall
  clock is never read, so a computer whose clock is off neither hides nor prolongs a
  confirmation. The countdown never disables Approve: the server's 410 decides.
- Resolved/expired: the card keeps its container (`ring-foreground/10`) and its headline
  becomes the receipt — a tone pill (Approved / Rejected / Expired, or Resolved elsewhere / Started / Not run) + `text-xs font-normal
  text-faint` "by alice · 14:02" — followed by the request summary, what the run produced
  (approved: the verdict and narration in a muted inset, its rows, and its trace collapsed as
  "Ran after approval in 1.2 s") or a one-line `text-xs text-faint` note, and links to the
  audit trail.

### 10.7 Audit log table

- Page header + filter bar (search fields for user, target, request ID and grant ID — target and IDs
  in mono — and selects for outcome, route source and time window) + active-filter chips +
  list card table with its pagination bar (no result count line, 4.11), under the
  Routing overview stats (5.3). Filters, page and the open entry live in
  the URL.
- Columns (none sortable): **Time** (a button that opens the entry: `tabular-nums
  text-muted-foreground`, local 24h `YYYY-MM-DD HH:mm:ss`) · **User** (`font-medium
  text-foreground`) · **Target** (tool id in `font-mono text-xs text-foreground`, plus a
  `rounded-sm bg-warning/10 px-1 text-xs text-warning` "write" tag for writes) · **Route**
  (route-source tag) · **Outcome** (status pill: `ok` success; `needs_confirmation`, `blocked`,
  `invalid`, `clarify` warning; `error` destructive; `started`, `chat` neutral) · **Rows**
  (right, `tabular-nums`) · **Request** (short request id, `font-mono text-xs text-faint`;
  click filters the log to that request) · **ID** (`#n`, right, `font-mono text-xs text-faint`).
- Opening an entry shows a right-hand sheet (4.7), "Entry #n", with time and user under the
  title, then: the facts as a `dl` (outcome, route source, target, rows, user and roles, time,
  problem, grant, request, parent, params digest), Parameters (`dl grid-cols-[auto_1fr] rounded-md
  bg-muted/40 p-2.5`, values `font-mono text-xs`; a masked value shows a lock tag), Trace
  replay (the trace list of 10.3, open) and Request chain (the records that share the request).
- Read-only; no inline edit affordances.

### 10.8 Schema-driven data table

- Container `overflow-hidden rounded-xl border border-border bg-card` (in chat) or the list
  card recipe (on pages).
- Header strip `flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-3
  py-1.5`. What it holds depends on the table:
  - A result table in chat: the provenance label `text-caption font-medium text-foreground`
    and meta `text-caption text-faint` ("128 rows · as of …", 10.4).
  - The Data table: the entity label `text-caption font-medium text-foreground`, the entity id
    `font-mono text-caption text-faint` and, while filters are set, "N filters" `text-caption
    text-faint`. It has no row count and no as-of; the total is in the pagination bar ("*from*–*to*
    of *total*", 4.11), once.
  - Not built yet: an export button `ml-auto rounded-lg px-2 py-1 text-caption font-medium
    text-muted-foreground hover:bg-accent hover:text-foreground` with `Download size-3.5`.
- `th` `sticky top-0 h-10 bg-card whitespace-nowrap px-4 py-2.5 text-caption font-medium
  tracking-wide text-muted-foreground` (a result table in chat scrolls past 24rem with its
  header fixed); `td` `border-b border-border/60 px-4 py-2.5` (no border on the last row);
  rows `hover:bg-muted/50`.
- Column type → rendering: string left · integer/decimal/currency right `tabular-nums`
  (unit in header) · date/time `tabular-nums` · enum → outline badge · boolean → dot · the Data primary key → a `font-medium text-foreground` button that opens the record sheet,
  `hover:text-primary hover:underline` (field types are `str int float bool date enum`; chat tables
  infer numbers and ISO dates from the values)
  · null/empty → `—` in `text-faint`.
- Optional scope footnote `border-t px-3 py-1.5 text-xs text-faint`.
- Scroll inside the container (`overflow-auto`: sideways, and down under the sticky header once
  height-capped: 24rem in chat, `max(24rem, calc(100svh - 18rem))` on Data); headers never wrap.

### 10.9 Import wizard (upload → preview → confirm → done)

- **Steps indicator**: `text-caption` row "1 Upload · 2 Preview · 3 Confirm · 4 Done"
  (`flex flex-wrap items-center gap-x-2 gap-y-1`, faint "·" separators); current `font-medium
  text-foreground`, done `text-success` with `Check size-3.5 stroke-[2.5]`, pending
  `text-faint`.
- **Upload** (a page step, not a dialog): a target select, the columns it reads as `h-6` chips
  (required ones `bg-secondary` with a red `*`), then a drop zone `rounded-2xl
  border-2 border-dashed px-6 py-10 text-center` (`border-border bg-background`; while a file
  is dragged over, `border-primary bg-accent/50`) with `FileUp size-6 stroke-[1.75] text-faint`,
  a "Choose file" button (the keyboard path) and the limits line `text-xs text-faint`; while
  parsing, `LoaderCircle size-6 animate-spin` and `text-xs text-muted-foreground` "Parsing and
  validating…".
- **Preview**: stats line `text-xs text-muted-foreground` — "**240** rows · **236** valid ·
  " + `text-destructive` "4 errors (won't be imported)" (numbers `font-semibold tabular-nums
  text-foreground`). Table in `max-h-80 overflow-auto rounded-lg ring-1 ring-border`, all
  cells `text-xs`; columns **Row** (`tabular-nums
  text-muted-foreground`), **Status** (outline badge: `New` / `Update` / `Valid` muted, `Error` with
  `border-destructive/40 text-destructive`), then schema columns `text-muted-foreground`.
  Error rows `bg-destructive/10` (hover unchanged) followed by a reason sub-row
  (`colSpan`, `py-1.5`): its errors in `text-xs text-destructive`, joined with "; "
  (no warnings); the cells at fault are `aria-invalid`, `bg-destructive/15 text-destructive`.
- **Confirm** (end of the preview): `flex flex-wrap items-center gap-x-3 gap-y-2`: ink button
  "Import 236 rows" (`h-9 px-3.5`, with a spinner while committing) and `outline` "Choose another file"
  (`h-9 px-3.5 text-muted-foreground`). Footnote `text-xs text-faint` on what will happen.
  Importing then raises the approval card (10.6) for this file.
- **Result**: `text-nav font-medium text-success` with `Check size-4 stroke-[2.5]` ("236 rows
  imported into *target*"), then the skipped-rows line `text-xs text-warning`, a `text-xs text-faint`
  meta line, `outline sm` "View in Data" / "View in Audit" and ink `sm` "Import another file". A
  rejected or expired confirmation returns to Upload with a `role="status"` note `rounded-lg bg-muted
  px-3 py-2 text-xs text-muted-foreground` ("Import cancelled. Nothing was written.").

---

## Appendix A — `globals.css`

Mirrors `web/src/app/globals.css` (minus its one-line header comment). The display serif relies on
`--font-libre-baskerville`, which `next/font/local` sets on `<html>` in `web/src/app/layout.tsx`
(§3.1); the other families are system stacks.

```css
@import "tailwindcss";
@import "tw-animate-css";
@import "shadcn/tailwind.css";

@custom-variant dark (&:is(.dark *));

@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --font-sans: var(--font-ui);
  --font-heading: var(--font-ui);
  --font-serif: var(--font-display);
  --font-mono: var(--font-code);

  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-faint: var(--faint);
  --color-faint-foreground: var(--faint-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-destructive-foreground: var(--destructive-foreground);
  --color-success: var(--success);
  --color-success-foreground: var(--success-foreground);
  --color-warning: var(--warning);
  --color-warning-foreground: var(--warning-foreground);
  --color-info: var(--info);
  --color-info-foreground: var(--info-foreground);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-input-background: var(--input-background);
  --color-switch-background: var(--switch-background);
  --color-ring: var(--ring);
  --color-chart-1: var(--chart-1);
  --color-chart-2: var(--chart-2);
  --color-chart-3: var(--chart-3);
  --color-chart-4: var(--chart-4);
  --color-chart-5: var(--chart-5);
  --color-sidebar: var(--sidebar);
  --color-sidebar-foreground: var(--sidebar-foreground);
  --color-sidebar-primary: var(--sidebar-primary);
  --color-sidebar-primary-foreground: var(--sidebar-primary-foreground);
  --color-sidebar-accent: var(--sidebar-accent);
  --color-sidebar-accent-foreground: var(--sidebar-accent-foreground);
  --color-sidebar-border: var(--sidebar-border);
  --color-sidebar-ring: var(--sidebar-ring);

  --radius-sm: calc(var(--radius) * 0.6);
  --radius-md: calc(var(--radius) * 0.8);
  --radius-lg: var(--radius);
  --radius-xl: calc(var(--radius) * 1.4);
  --radius-2xl: calc(var(--radius) * 1.8);
  --radius-3xl: calc(var(--radius) * 2.2);
  --radius-4xl: calc(var(--radius) * 2.6);

  /* type tokens (rem only; the root font-size is the scale knob) */
  --text-caption: 0.8125rem;
  --text-caption--line-height: 1.4;
  --text-nav: 0.9375rem;
  --text-answer: 1.0625rem;
  --text-page-label: 1.1875rem;

  --ease-standard: cubic-bezier(0.4, 0, 0.2, 1);
}

:root {
  --font-size: 16px;
  /* One web font: layout.tsx sets --font-libre-baskerville on <html> (Latin only, with its
     metric-matched fallback face). Everything else is a system font. Each stack lists its Latin
     families first, so English, and the digits and Latin words inside Chinese text, keep one
     design in both languages; the CJK families after them only supply the glyphs the Latin ones
     lack. A CJK family placed first would set English in its own Latin glyphs instead. */
  --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial,
    "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
  --font-display: var(--font-libre-baskerville), Georgia, "Songti SC", STSong, SimSun,
    "Noto Serif CJK SC", serif;
  --font-code: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;

  --background: #fafafa;
  --foreground: #1a1a1a;
  --card: #ffffff;
  --card-foreground: #1a1a1a;
  --popover: #ffffff;
  --popover-foreground: #1a1a1a;
  --primary: #2f6bed;
  --primary-foreground: #ffffff;
  --secondary: #f0f0f0;
  --secondary-foreground: #1a1a1a;
  --muted: #f0f0f0;
  --muted-foreground: #5c5c5c;
  --faint: #6a6a6a;
  --faint-foreground: #6a6a6a;
  --accent: #e8effc;
  --accent-foreground: #2a5fd4;
  --destructive: #dc2626;
  --destructive-foreground: #ffffff;
  --success: #15803d;
  --success-foreground: #ffffff;
  --warning: #b45309;
  --warning-foreground: #ffffff;
  --info: #2f6bed;
  --info-foreground: #ffffff;
  --border: #e5e5e5;
  --input: #e5e5e5;
  --input-background: #ffffff;
  --switch-background: #d4d4d4;
  --ring: #2f6bed;
  --chart-1: #2f6bed;
  --chart-2: #6f9bf2;
  --chart-3: #38a3d1;
  --chart-4: #737373;
  --chart-5: #b4b4b4;
  --radius: 0.75rem;
  --sidebar: #f7f7f7;
  --sidebar-foreground: #5c5c5c;
  --sidebar-primary: #2f6bed;
  --sidebar-primary-foreground: #ffffff;
  --sidebar-accent: #ebebeb;
  --sidebar-accent-foreground: #1a1a1a;
  --sidebar-border: #e5e5e5;
  --sidebar-ring: #2f6bed;

  --ease-standard: cubic-bezier(0.4, 0, 0.2, 1);
  --ai-glow-a: #4facfe;
  --ai-glow-b: #00f2fe;
  --ai-glow-grad: linear-gradient(130deg, var(--ai-glow-a), var(--ai-glow-b));

  /* z-index scale (DESIGN 5.4, formalized) */
  --z-sidebar: 20;
  --z-header: 20;
  --z-split: 30;
  --z-panel: 40;
  --z-overlay: 50;
}

.dark {
  --background: #1a1a1a;
  --foreground: #ebebeb;
  --card: #242424;
  --card-foreground: #ebebeb;
  --popover: #242424;
  --popover-foreground: #ebebeb;
  --primary: #5b8cf5;
  --primary-foreground: #12151b;
  --secondary: #2e2e2e;
  --secondary-foreground: #ebebeb;
  --muted: #2e2e2e;
  --muted-foreground: #a3a3a3;
  --faint: #999999;
  --faint-foreground: #999999;
  --accent: #23324e;
  --accent-foreground: #9db8f5;
  --destructive: #f0616d;
  --destructive-foreground: #1a1a1a;
  --success: #46b877;
  --success-foreground: #12151b;
  --warning: #d99a4e;
  --warning-foreground: #12151b;
  --info: #5b8cf5;
  --info-foreground: #12151b;
  --border: #333333;
  --input: #383838;
  --input-background: #242424;
  --switch-background: #383838;
  --ring: #5b8cf5;
  --chart-1: #5b8cf5;
  --chart-2: #7fa8f5;
  --chart-3: #56b4dd;
  --chart-4: #a3a3a3;
  --chart-5: #6b6b6b;
  --sidebar: #1f1f1f;
  --sidebar-foreground: #a3a3a3;
  --sidebar-primary: #5b8cf5;
  --sidebar-primary-foreground: #12151b;
  --sidebar-accent: #2e2e2e;
  --sidebar-accent-foreground: #ebebeb;
  --sidebar-border: #333333;
  --sidebar-ring: #5b8cf5;
  color-scheme: dark;
}

@utility z-sidebar {
  z-index: var(--z-sidebar);
}
@utility z-header {
  z-index: var(--z-header);
}
@utility z-panel {
  z-index: var(--z-panel);
}
@utility z-overlay {
  z-index: var(--z-overlay);
}

@layer base {
  * {
    @apply border-border outline-ring/50;
  }
  html {
    font-size: var(--font-size);
    @apply font-sans;
  }
  body {
    @apply bg-background text-foreground antialiased;
  }
  [data-slot="input"],
  [data-slot="textarea"],
  [data-slot="select-trigger"] {
    @apply bg-input-background;
  }
  details > summary::-webkit-details-marker {
    display: none;
  }
  /* Coarse pointers get 44px targets on chips, row actions and nav items (DESIGN 8). */
  @media (pointer: coarse) {
    [data-touch-target] {
      min-height: 44px;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    *,
    ::before,
    ::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
      scroll-behavior: auto !important;
    }
    ::view-transition-old(*),
    ::view-transition-new(*),
    ::view-transition-group(*) {
      animation-duration: 0.01ms !important;
      animation-delay: 0s !important;
    }
  }
}

@keyframes ai-float-in {
  from {
    opacity: 0;
    transform: translateY(2px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
@keyframes ai-exit {
  to {
    opacity: 0;
    transform: scale(0.98);
  }
}
@keyframes ai-breathe {
  0%,
  100% {
    opacity: 0.15;
  }
  50% {
    opacity: 0.22;
  }
}
@keyframes ai-dot {
  0%,
  60%,
  100% {
    opacity: 0.25;
    transform: translateY(0) scale(0.85);
  }
  30% {
    opacity: 1;
    transform: translateY(-3px) scale(1);
  }
}

@layer components {
  .ai-enter {
    animation: ai-float-in 200ms var(--ease-standard) both;
  }
  .ai-exit {
    animation: ai-exit 150ms var(--ease-standard) both;
  }

  .ai-glow {
    position: relative;
  }
  .ai-glow::before {
    content: "";
    position: absolute;
    inset: -4px;
    z-index: -1;
    border-radius: inherit;
    background: var(--ai-glow-grad);
    filter: blur(24px);
    opacity: 0;
    pointer-events: none;
    will-change: opacity;
    transition: opacity 150ms var(--ease-standard);
  }
  .ai-glow[data-active]::before {
    opacity: 0.15;
    transition-duration: 200ms;
    animation: ai-breathe 2.6s var(--ease-standard) 200ms infinite;
  }

  .ai-dots {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    flex-shrink: 0;
  }
  .ai-dots > i {
    display: block;
    width: 4px;
    height: 4px;
    border-radius: 50%;
    background: currentColor;
    opacity: 0.3;
    animation: ai-dot 1.2s var(--ease-standard) infinite;
  }
  .ai-dots > i:nth-child(2) {
    animation-delay: 0.15s;
  }
  .ai-dots > i:nth-child(3) {
    animation-delay: 0.3s;
  }

  .report-prose {
    @apply text-sm leading-relaxed text-foreground;
  }
  .report-prose > :first-child {
    @apply mt-0;
  }
  .report-prose > :last-child {
    @apply mb-0;
  }
  .report-prose h1 {
    @apply mt-0 mb-3 font-serif text-lg font-semibold;
  }
  .report-prose h2 {
    @apply mt-8 mb-2 text-base font-semibold;
  }
  .report-prose h2:first-child {
    @apply mt-0;
  }
  .report-prose h3 {
    @apply mt-5 mb-1.5 text-sm font-semibold;
  }
  .report-prose p {
    @apply my-3 text-muted-foreground;
  }
  .report-prose ul {
    @apply my-3 list-disc space-y-2 pl-4 marker:text-faint;
  }
  .report-prose ol {
    @apply my-3 list-decimal space-y-3 pl-6 marker:font-semibold marker:text-muted-foreground marker:tabular-nums;
  }
  .report-prose li {
    @apply leading-relaxed text-muted-foreground;
  }
  .report-prose strong {
    @apply font-semibold text-foreground;
  }
  .report-prose a {
    @apply text-primary underline underline-offset-2 hover:text-foreground;
  }
  .report-prose hr {
    @apply my-4 border-border;
  }
  .report-prose code {
    @apply rounded bg-muted px-1 py-0.5 font-mono text-xs;
  }
  .report-prose pre {
    @apply my-3 overflow-x-auto rounded-lg border border-border bg-muted/40 px-3 py-2 font-mono text-xs;
  }
  .report-prose pre code {
    @apply bg-transparent p-0;
  }
  .report-prose blockquote {
    @apply my-5 rounded-xl border-l-0 bg-accent/60 px-4 py-3 font-medium text-foreground ring-1 ring-primary/15;
  }
  .report-prose table {
    @apply my-3 w-full border-separate border-spacing-0 overflow-hidden rounded-xl tabular-nums ring-1 ring-border;
  }
  .report-prose th {
    @apply border-b border-border bg-muted/40 px-3 py-2 text-left font-medium text-muted-foreground;
  }
  .report-prose td {
    @apply border-b border-border/60 px-3 py-2 text-foreground/90;
  }
  .report-prose tr:last-child td {
    @apply border-b-0;
  }

  .answer-prose {
    @apply text-answer font-semibold;
  }
  .answer-prose p,
  .answer-prose li {
    @apply font-semibold text-foreground;
  }
  .answer-prose h2 {
    @apply text-xl;
  }
  .answer-prose h3 {
    @apply text-lg;
  }
}
```

---

## Appendix B — Gaps & open items

| Item | Status |
|---|---|
| Root font-size preference | `--font-size` (16px) is the knob; nothing lets a user set it yet. Intended steps 14 / 15 / 16 / 18 / 20 px; verify key screens at 20px when it lands |
| Dark mode | Built: tokens complete, `next-themes` class strategy, Light / Dark / System menu in the top bar. QA every new pattern in both themes |
| Z-index scale | Built as `--z-*` tokens with `z-sidebar`, `z-header`, `z-panel`, `z-overlay` utilities; `--z-split` has no utility yet; shadcn menu, tooltip and sheet primitives keep a literal `z-50` |
| Mobile sidebar / touch targets | Built: sheet below `md`, 64px rail below `lg`, `[data-touch-target]` 44px minimum on coarse pointers. Mark every new chip or row action |
| Hard-coded px font sizes, raw palette status colours | None in `web/src`; keep it that way (§3.2, §7) |
| Text contrast below WCAG AA | Not covered by the guarantees in §2.6, and below AA for small text in at least one theme: soft tone text (`bg-{tone}/10 text-{tone}` in the badge, status pill, notice banner and audit "write" tag; dark `/20`), the tool id (`opacity-70`) and the "· writes" tag (`opacity-80`) on a clarification chip, and the inactive options of the segmented control (`text-foreground/60`). Do not copy these recipes for new small text; raising them is on the [roadmap](../ROADMAP.md) |
| Console route transitions | None defined; navigation is instant unless a spec is added |
| Typography across locales | Verify line lengths, truncation and wrapping in both `en` and `zh`, and again at 14–20px roots once the font-size preference exists |
| Specified here, not built in `web/` yet | KPI tile (4.10), progress bar, ring gauge and health card (4.12), popover (4.7), search suggestion list (4.11), KPI / status grids (5.3), split view (5.2), chat side panel, task list, attached-context chips and drag-and-drop overlay (10.2), multi-question clarify variant (10.5), diffs or affected rows in the approval card (10.6), data-table export button (10.8), page-header back button (4.9), result count line (4.11), `.ai-exit` exit animation (6.3). Also not built, and not specified in §1–10: a dots-only live trace for simple routes (every route shows its lines today), and, for provenance, a collapsed "Sources & scope" block holding the intent anchor (current tool label + a re-run dropdown), a data-nature line ("Includes database results" / "General knowledge · not company data") and external references. Today provenance is the one line (or table strip) under the answer (10.4) |

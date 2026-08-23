---
name: ui-design-standards
description: Every app ships a beautiful, APPROPRIATE UI (fits the company + its users — ask that first), built for desktop/tablet/mobile from the start, responsive with a genuine mobile-first mobile experience and a proper desktop experience. Execution leans on the frontend-design skill.
when_to_read: before building or restyling ANY user-facing UI on a product-track project (the default) — to set design direction, breakpoints, and the polish bar before writing components
sections:
  - When this applies
  - Step 0 — design direction FIRST (ask before pixels)
  - Beautiful = appropriate, not generic
  - User-first & uncluttered (assume non-technical users)
  - Multi-device from the start (mobile-first + desktop-proper)
  - The polish bar (what "looks really good" means)
  - Accessibility is part of "good"
  - Verify on real viewports
  - Per-UI checklist
---
# Beautiful, Appropriate, Multi-Device UI — by default

> Navigation: read this frontmatter + section list, then `grep` the `## ` heading you need. This doc is the STANDARD (the WHAT/bar). For execution technique — typography, layout, taste, avoiding templated defaults — use the **`frontend-design` skill**; this doc says what's required and links out to it.

**The rule:** every app we build ships a **beautiful** user interface. "Beautiful" does **not** mean generically pretty — it means a **good, visually appealing design that fits the company and the people who will actually use it**. That fit is a set of questions to answer *first*. And every app is **properly built for desktop, tablet, and mobile from the start** — responsive, but with a genuine **mobile-first** mobile experience and a **proper desktop** experience, not one layout stretched or shrunk.

## When this applies

- **Default = on** for any product-track project with a UI (product-track is itself the default — see `api-mcp-first.md`). A **throwaway/prototype** may opt out via `PROJECT_SCOPE.md` (*Product Track: NO*), and a backend-only/library/CLI project simply has no UI to which this applies.
- **The design INTENT is always-on; the DEPTH scales to stage.** Every UI is on-brand, user-first, and works on mobile + desktop from the first screen — cheap and non-negotiable. But exhaustive edge-state polish, full tablet/breakpoint passes, and deep a11y audits **deepen as the project matures** (`PROJECT_SCOPE.md` → *Stage*): a prototype ships clean / usable / on-brand, not gold-plated. A tiny copy tweak is Trivial; a new screen at a mature stage ships to the full bar below.

## Step 0 — design direction FIRST (ask before pixels)

"Beautiful" is meaningless without a target. Before building UI, establish (ask the user; infer + confirm if they don't know):

- **Who is the company / what's the brand?** Personality/tone (e.g. calm-clinical, bold-consumer, serious-fintech, playful), existing brand assets (logo, colors, type, voice), any brand guide.
- **Who are the users, and in what context?** Expertise, device mix, environment (on-the-go phone use vs. focused desktop work), accessibility needs, emotional state (a therapy client vs. a power admin want very different feels).
- **References & guardrails:** 2–3 aesthetic references or competitors to echo or avoid; any hard constraints (existing design system, accessibility/compliance, dark mode).

Capture the answers in `PROJECT_SCOPE.md` (a short **Design Direction** note) so every future screen inherits the same intent instead of re-deciding per feature. If the user is unsure, propose a direction with rationale and confirm — don't default to a generic template look.

## Beautiful = appropriate, not generic

- Match the **audience and brand**, not a trend. A polished admin tool and a warm consumer app are both "beautiful" when each fits its users — and each is ugly wearing the other's skin.
- **Avoid the templated/default-framework look** (the frontend-design skill exists for exactly this). Intentional typography, spacing, color, and hierarchy — not stock component defaults dropped on a page.
- **Consistency reads as quality:** one design system — tokens for color, type scale, spacing, radius, elevation — reused everywhere. Inconsistency looks unfinished even when each piece is fine.

## User-first & uncluttered (assume non-technical users)

**~99% of our users are non-technical.** The UI must be usable with no manual: a user should instantly see *where things are* and *what they do*.

- **Less over more.** A crowded, overloaded screen confuses. Show what matters for the task at hand; progressively disclose the rest. One clear primary action per screen. When in doubt, remove — don't add.
- **Intuitive by position and size.** Put controls where users expect them; use size, weight, and grouping so importance is obvious at a glance. Most-important action = most prominent; related things grouped; nothing critical buried in a menu.
- **Copy is UX, not decoration.** Plain, human language in the user's words — label buttons/sections for what they DO, never internal or technical jargon. Clear headings, guiding empty states, and error messages that say what to do next. Intuitive positioning + sizing + copy *together* are what "intuitive" means.
- **Guide, don't assume expertise.** Sensible defaults, an obvious next step, gentle inline guidance. Never require the user to understand a technical concept to succeed.

## Multi-device from the start (mobile-first + desktop-proper)

Build for **desktop, tablet, and mobile from day one** — never "desktop now, mobile later."

- **Responsive is the floor, not the goal.** A layout that merely reflows is not automatically good on a phone.
- **Mobile = mobile-first, designed for mobile.** Design the mobile experience on its own terms: thumb-reach and touch targets (≥44px), bottom-anchored primary actions, single-column focus, native input types, performance on a phone. It should *look and function best for a phone*, not look like a squeezed desktop.
- **Desktop = designed for desktop.** Use the space: multi-column/master-detail where it helps, denser information, hover/keyboard affordances, larger canvases. It should look and function properly for a large screen, not a stretched phone card.
- **Tablet** gets a deliberate in-between (often the awkward one — check it, don't assume it inherits correctly).
- Pick breakpoints intentionally and design each; don't let content break at arbitrary widths.

## The polish bar (what "looks really good" means)

- **Visual hierarchy:** clear primary action per screen; type scale and weight guide the eye; generous, rhythmic spacing.
- **Every state designed:** loading (skeletons), empty, error, success, disabled, long-content overflow — not just the happy path.
- **Motion with purpose:** subtle, fast transitions that aid understanding; respect `prefers-reduced-motion`.
- **Details:** aligned grids, consistent iconography, real content (not lorem) when judging density, no orphaned/cramped elements, dark mode if the brand implies it.

## Accessibility is part of "good"

Not optional polish — a beautiful UI that excludes users isn't done.

- Sufficient color contrast; never color-only signaling.
- Full keyboard operability; visible focus; semantic HTML/ARIA; labelled controls.
- Respect reduced-motion; readable base font sizes; hit targets sized for touch.

## Verify on real viewports

- After any UI/runtime change, end with the standard **browser-verification** offer (`code-quality.md`) — and actually check the layout at **mobile, tablet, and desktop widths**, not just the dev window. Playwright for interaction/responsive snapshots; Lighthouse for perf + a11y.
- The **front-end/back-end reconciliation audit** (`code-quality.md`) still applies: every capability has a correct, wired UI surface across breakpoints.

## Per-UI checklist

- [ ] Design direction established (brand/tone + users/context + references) and recorded in `PROJECT_SCOPE.md`.
- [ ] Fits the company and its actual users — appropriate, not a generic template look (used the `frontend-design` skill).
- [ ] One consistent design system (tokens + components) applied.
- [ ] Built for a non-technical user: obvious where everything is and what it does; no jargon.
- [ ] Uncluttered — less over more, one clear primary action per screen, progressive disclosure.
- [ ] Copy is plain-language and action-oriented (buttons, headings, empty/error states).
- [ ] Mobile designed mobile-first (touch targets, thumb reach, single-column focus) — best-for-phone, not shrunk desktop.
- [ ] Desktop designed for desktop (space used, density, hover/keyboard) — not a stretched phone.
- [ ] Tablet checked deliberately.
- [ ] All states designed (loading/empty/error/success/disabled/overflow).
- [ ] Accessible (contrast, keyboard, focus, semantics, reduced-motion).
- [ ] Verified at mobile + tablet + desktop widths in a real browser.

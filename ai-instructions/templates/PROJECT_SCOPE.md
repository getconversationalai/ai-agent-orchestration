# [Project Name] — Project Scope

## Product Track & Stage

**Product Track: YES** (default) — *architected* to become a real product: features are built as clean, transport-agnostic capabilities so a public API + MCP are cheap to add later (`api-mcp-first.md`), with a beautiful, user-first, multi-device UI (`ui-design-standards.md`).
<!-- Set to NO only for a genuine throwaway / prototype / experiment with no product future. When unsure, leave YES. -->

**Stage: Prototype**  ← set one: **Prototype · MVP · Growth · Mature**. This gates how much you BUILD now (`api-mcp-first.md` → "Design for scale, build for stage"), NOT the architecture:
<!--
- Prototype / pre-users: UI + capability layer + cheap security fundamentals. DEFER the public API, MCP, heavy scalability plumbing, and full security-review apparatus. Features at their real size (usually Trivial/Small) — no spec/plan docs, no multi-agent (coder->reviewer->fix->re-review) pipeline.
- MVP / early users: add the public API + MCP for capabilities a consumer actually needs; full security review on genuinely sensitive surfaces (auth/tenant/money).
- Growth / Mature: new capabilities ship all three surfaces by default; full scalability + security apparatus where risk warrants.
-->

### Design Direction
<!-- Establish BEFORE building UI (ui-design-standards.md → Step 0). Fill in:
- Brand / tone: (e.g. calm-clinical · bold-consumer · serious-fintech · playful)
- Users & context: (who they are, expertise, device mix, environment, accessibility needs)
- References / guardrails: (2–3 aesthetic refs or competitors to echo/avoid; existing design system; dark mode?)
-->

## Structure

```
# Describe your project's directory structure here
src/
├── ...
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | |
| Backend | |
| Database | |
| Hosting | |

## Key Database Tables

<!-- List your main tables, e.g.: users, organizations, projects, ... -->

## Project Structure Conventions

<!-- Describe how code is organized: feature folders, naming patterns, barrel exports, etc. -->

## Code Style Rules

<!-- List the conventions agents must follow, e.g.:
- Use existing UI components before creating custom ones
- Use [ORM] for all DB queries — no raw SQL
- Use [validation lib] for all input validation
-->

## Documentation Guide

| Document | Purpose | When to Read |
|----------|---------|-------------|
| `plans/phase-*.md` | Task breakdowns, schemas, acceptance criteria | Primary reference — read before any work |
| `Product-And-Tech-Spec.md` | Requirements, API contracts, UX specs | When you need exact field definitions |

## Hub Files (Parallel Agent Coordination)

<!-- These files are modified by nearly every feature. When running parallel agents, handle these centrally:
- src/app/routes.ts
- src/types/index.ts
- ...
-->

## Migration File Naming

<!-- If using parallel agents with DB migrations:
Each agent must use a unique migration prefix (e.g., Agent A gets 0010_, Agent B gets 0020_).
Include this in the agent's prompt. Never let agents auto-number — they'll collide.
-->

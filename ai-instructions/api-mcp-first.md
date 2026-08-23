---
name: api-mcp-first
description: Design for scale, build for stage. ALWAYS architect a feature as a transport-agnostic capability (typed contract) so a public API + MCP are cheap to add; BUILD those surfaces + the full apparatus proportional to project stage & real need. Product-track (= architect-for-it) is the default; throwaways opt out in PROJECT_SCOPE.
when_to_read: before designing or building ANY feature on a product-track project (the default) — to structure it as a capability AND to right-size which surfaces/process the project's stage actually calls for
sections:
  - When this applies
  - Design for scale, build for stage (the staging gate)
  - The core pattern — transport-agnostic capability layer
  - Build order per feature
  - Public API baseline (when you build it)
  - MCP baseline (when you build it)
  - Scalability baseline (grows with load)
  - Cost, ceremony & tier (this does NOT force Large)
  - Per-feature checklist
---
# API + MCP-First, Scalable-by-Default Feature Architecture

> Navigation: read this frontmatter + section list, then `grep` the `## ` heading you need and read only that section. Do NOT read the whole doc. This doc is HOW; the always-on router bullet is the WHAT. Security details for every surface live in `security.md`. **Read "Design for scale, build for stage" first — it stops this doc from over-applying to small/early work.**

**The rule:** most projects here become larger products, so **every project is product-track by default** — but *product-track* means **architect** every feature so it *can* become (1) **scalable** and (2) **reachable through a public API + MCP** cheaply, later. It does **not** mean **build** the whole product apparatus on feature #1. The always-cheap part — the clean capability layer — you do from the first feature. *Which surfaces you actually build now, and how much process you spend, scale to project stage* (next sections).

**Why the architecture is affordable:** the failure mode is building **UI-first**, where logic ends up trapped in React handlers and route controllers, and the API/MCP later become drift-prone reverse-engineering jobs. Invert it: the **capability** — a typed, transport-agnostic operation — is the unit of work; UI / API / MCP are thin adapters that project it. Build the core once → each surface is a thin adapter you can add whenever the stage calls for it.

## When this applies

- **Default = product-track.** Treat every project as a future product and apply this doc UNLESS its `PROJECT_SCOPE.md` marks it a throwaway/prototype/experiment (*Product Track: NO*), in which case you may skip the capability split entirely.
- **"Product-track" governs ARCHITECTURE, not how much you build today.** It means: structure code as capabilities so scaling up is cheap. It does **not** mean every feature ships a public API + MCP + full scalability + full security review on day one — that scales to stage (next section).
- Building an API/MCP adapter, or having a service layer, does **NOT** by itself make a feature Large/Risky tier or trigger spec/plan docs or a multi-agent (coder→reviewer→fix→re-review) pipeline. **Tier follows the capability's *real* risk** (auth / tenant-isolation / money / migrations / webhooks / genuine security) **+ size**, per the ceremony-tier rule. A simple pre-launch capability stays Trivial/Small.

## Design for scale, build for stage (the staging gate)

Two independent decisions — never conflate them:

- **HOW you structure the code** → always the clean, transport-agnostic capability layer (below). Nearly free; do it from feature #1.
- **WHICH surfaces you build now + how heavy the process is** → scales to **project stage** (`PROJECT_SCOPE.md` → *Stage*) and whether a surface is actually needed yet.

| Stage | Build now | Defer until needed | Process weight |
|-------|-----------|--------------------|----------------|
| **Prototype / first features / pre-users** | UI + clean capability/service layer + the **cheap security fundamentals** (no committed secrets; tenant-scope multi-tenant queries; validate inputs; fail closed) | public API, MCP server, API versioning, heavy scalability plumbing, full security-review apparatus | Features at their **real size** (usually Trivial/Small): implement directly in a worktree, one brief inline plan, self-review. **No** spec/plan docs, **no** multi-agent review pipeline just because the roadmap is ambitious. |
| **MVP / early real users** | the above + public API/MCP for the capabilities an external consumer actually needs; full security review on genuinely sensitive surfaces (auth/tenant/money) | broad scale-out infra until load is real | ceremony by **real risk + size** |
| **Growth / mature** | new capabilities ship all three surfaces by default; full scalability + security apparatus | — | full apparatus where risk warrants |

**Because each feature is architected as a capability, a deferred surface is a thin adapter added later — never a refactor.** That is what makes deferring safe: you are *sequencing* the build, not taking on debt.

**When unsure of stage, build lighter.** You can always add the API/MCP/scale later precisely because the capability layer is already there — you cannot get back time spent gold-plating a product with no users yet. (This mirrors the global "ceremony scales to change size" and "robustness by default *without* over-building" intent — right-size, don't max-size.)

## The core pattern — transport-agnostic capability layer

*(This is the always-on architecture — applies at every stage, including prototype.)* Business logic lives in a **service/capability layer**, never in a route handler or a UI component.

- **A capability = one named operation with a typed contract:** input schema, output schema, authorization rule, error set, declared side effects (zod/pydantic/etc. as the single source of truth).
- **Thin adapters over the same capability:** **UI** (calls the capability; no business rules in the component), and — when the stage calls for them — **public API** (versioned HTTP endpoint) and **MCP** (one tool, schema derived from the same contract).
- Adapters are **mechanical projections** of the contract. If writing an adapter requires re-deriving business logic, the logic is in the wrong layer — move it into the capability.
- **Expose capabilities, not the database.** No adapter reaches raw tables; everything goes through the capability (tenant-scoped and authorized).

```
                     ┌─────────────── capability (service layer) ───────────────┐
   UI adapter ─────► │  typed contract: input · output · authz · errors · effects │
   API adapter ────► │  tenant-scoped business logic, testable in isolation       │  ← API/MCP adapters
   MCP adapter ────► │  (the ONE place the behavior is defined)                    │    added when stage needs them
                     └────────────────────────────────────────────────────────────┘
```

## Build order per feature

1. **Define the contract** — input/output schema, authz, errors. (At Trivial/Small early-stage work this is a couple of lines inline, not a spec document.)
2. **Implement the capability** in the service layer — tenant-scoped, no transport concerns, unit-testable on its own.
3. **Wire the adapters the stage calls for, in the same phase** — always **UI**; add **API + MCP** once past prototype / when a consumer needs them. A deferred surface is a thin adapter added later, not a refactor.
4. **Test proportionally** — capability-layer behavior test always; adapter smoke tests for each surface you built.

**Anti-pattern that this guards against:** burying logic in a React handler or a single route controller so that adding an API/MCP later means reverse-engineering and divergent behavior. Keeping logic in the capability is what makes "defer the surface" safe. (Deferring the *surface* is fine; trapping the *logic* is not.)

## Public API baseline (when you build it)

- **Versioned** (`/v1/...`); contracts evolve **additively** (expand-contract — `renames-and-schema-changes.md`).
- **Auth & tenancy:** API keys / OAuth scopes, server-derived identity, tenant-scoping on every call (`security.md` §2/§3/§4). Never trust a client-asserted tenant.
- **Validation & errors:** schema-validate at the boundary (`security.md` §5); one consistent error envelope + correlation id; no internals leaked.
- **Scale semantics:** cursor pagination, filtering/sorting, **idempotency keys on writes** (`security.md` §16), **per-tenant rate limits** (`security.md` §8).
- **Predictable resources**; **docs generated from the schemas** (OpenAPI) so they can't drift.

## MCP baseline (when you build it)

- **One tool per capability**, schema **derived from the same contract** as the API — not a parallel hand-written definition.
- **Authorized at the caller's identity/tenant**, least privilege; read-only vs write split; write/irreversible tools may require human approval (`security.md` §6).
- **Agent-consumable:** clear tool names + descriptions, structured outputs, bounded results.
- **Treat MCP tool input as untrusted** — prompt-injection carrier; the capability re-validates and re-authorizes (`security.md` §6). Never trust tool-supplied ids/amounts without server-side checks.

## Scalability baseline (grows with load)

Apply proportionally — a pre-user prototype needs a **tenant-scoped, sensibly-indexed, paginated** data model (cheap, don't skip); the heavier items below phase in as real load appears.

- **Stateless services** (horizontal scale) — no in-memory state that blocks multiple instances. *(Cheap; adopt early.)*
- **Tenant-scoped data model + indexes** matched to real query patterns. *(Cheap; adopt early.)*
- **Pagination + bounded queries** everywhere; kill N+1s. *(Cheap; adopt early.)*
- **Async/queue** for slow/fan-out work; **idempotent** handlers. *(When the work exists.)*
- **Caching with explicit invalidation**; **rate-limit + cost-bound** expensive/AI/outbound work per tenant (`security.md` §8). *(When load/cost is real.)*
- **Observability per capability** (structured logs, metrics, tracing). *(Grows with the system.)*
- **Config over hardcoding** — no hardcoded URLs/secrets (`security.md` §7). *(Always.)*

## Cost, ceremony & tier (this does NOT force Large)

- The reason the three surfaces are affordable *when you build them* is that the capability is built once and adapters are projections — not three implementations. But affordable ≠ mandatory-now: **build the surfaces the stage calls for.**
- **This doc never forces Large-tier ceremony.** A feature's tier = **real risk + size** (ceremony-tier rule) — the presence of an API/MCP adapter or a service layer is not itself a risk trigger. An early-stage CRUD capability is Trivial/Small; implement it directly, no spec/plan/multi-agent-review.
- **New capability, past prototype** → ships UI + API + MCP by default. **Prototype stage** → UI + capability layer is enough.
- When a formal plan *does* exist (a genuinely Large/Risky capability), it lists the contract, the adapter surfaces you're building, and the scalability considerations. The **front-end/back-end reconciliation audit** (`code-quality.md`) covers whatever surfaces exist (UI, and API/MCP once built).

## Per-feature checklist

Always (every stage):
- [ ] Logic lives in a transport-agnostic **capability** with a typed contract (input/output/authz/errors) — not in UI components or route handlers.
- [ ] UI adapter wired.
- [ ] Cheap security fundamentals honored (`security.md`: no committed secrets, tenant-scope, input validation, fail closed).
- [ ] Data model tenant-scoped, sensibly indexed, paginated.
- [ ] Feature sized/reviewed by its **real** risk + size (not auto-Large because it has an API).

Once past prototype / when a surface is needed:
- [ ] Versioned public API adapter wired (auth, validation, pagination, idempotency, error envelope).
- [ ] MCP tool wired (schema from the same contract, least-privilege auth, input treated as untrusted).
- [ ] Full scalability + security-review apparatus applied to genuinely sensitive/loaded surfaces.
- [ ] Contract documented via generated OpenAPI + MCP schema (no hand-drift).

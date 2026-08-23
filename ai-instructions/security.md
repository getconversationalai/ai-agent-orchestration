---
name: security
description: Building safe, secure & private apps — auth, multi-tenant isolation, AI/LLM & agent injection defense, webhooks, secrets, RLS, SSRF, concurrency, CI gates, governance. Fail-closed / verify-before-trust throughout.
when_to_read: before building OR reviewing any feature touching auth, multi-tenant data, an LLM/agent surface, webhooks, secrets, crypto, RLS, outbound fetches, money, or data handling
sections:
  - 0. The security mindset (read first)
  - 1. Pre-code security checklist
  - 2. Authentication & sessions
  - 3. Authorization & least privilege
  - 4. Multi-tenant isolation
  - 5. Input validation & output handling
  - 6. AI / LLM / agent security
  - 7. Browser & session security (CSRF, CORS, headers)
  - 8. Abuse resistance & rate limiting
  - 9. Webhooks & external callbacks
  - 10. Secrets & key management
  - 11. Data protection & privacy
  - 12. Database & row-level security
  - 13. Logging, audit & monitoring
  - 14. Error handling
  - 15. SSRF & outbound requests
  - 16. Concurrency & business-logic integrity
  - 17. CI/CD & supply chain
  - 18. Security review checklist (before merge)
  - 19. Governance & assurance
  - 20. Security definition of done
---
# Claude Security Protocols — Building Safe, Secure & Private Apps

> Navigation: read this frontmatter + section list, then `grep` the `## <n>.` heading you need and read only that section (Read with offset/limit). Do NOT read the whole doc. Cross-references use `§<n>`. The two invariants — **fail closed** and **verify before trust** — run through every section.

**A portable instruction set for any project.** Drop this file into a repo (or reference it from `CLAUDE.md` / `AGENTS.md`) to hold Claude — and the humans reviewing its work — to a consistent security standard. It is framework-agnostic; examples use common stacks (Node/Express, Postgres/Supabase, React) but the protocols apply anywhere.

**How to read the labels:**
- **[REQUIRED]** — a control whose absence is a real vulnerability. Do not ship without it (or an explicit, recorded risk-acceptance by a human owner).
- **[RECOMMENDED]** — strong hardening; adopt unless there's a reason not to.
- **[VERIFY]** — how to prove the control actually works (a control without a test is a claim, not a control).

**Two things this file will repeatedly insist on**, because they are the most common ways "secure-looking" code is actually insecure:
1. **Fail closed.** On error, ambiguity, or missing config, deny — never allow.
2. **Verify before trust.** Every identifier, token, signature, and ownership claim from outside your trust boundary is verified *before* it is acted on.

---

## 0. The security mindset (read first)

- **Assume the caller is hostile.** Every request, webhook, file, and field is attacker-controlled until proven otherwise. Validate at the boundary; re-verify ownership before use.
- **Untrusted content is data, not instructions.** Any content an LLM reads — user messages, tool results, retrieved pages, documents, AI-derived memories — and any action an LLM can take crosses a trust boundary. The model's context window is attacker-reachable; enforce the boundary *structurally*, not by instructing the model to behave (§6).
- **Defense in depth.** No single control is the whole defense. If one layer is the *only* thing preventing a breach (e.g., one hand-written filter), that's a design smell — add a second.
- **Least privilege everywhere.** Grant the minimum scope, role, and lifetime. Default-deny, then allow.
- **Security is proven, not asserted.** Pair every meaningful control with a test (ideally an adversarial one) and, where possible, an automated gate.
- **Risk-proportionate.** Size controls to the data's sensitivity and the applicable regulations. Don't gold-plate low-sensitivity data; don't under-protect credentials or PII. Record risk-acceptances as explicit decisions with a named owner.
- **Right-size to stage; this file is not a ceremony mandate.** The *cheap fundamentals* — don't commit secrets, tenant-scope every query, validate input, fail closed, verify identity server-side — apply at **every** stage, including a pre-user prototype. The *heavy apparatus* (adversarial-reviewer-per-task, full RLS/pgTAP matrices, pen-test, governance §19) scales with project **stage**, data sensitivity, and real risk. Touching "security" does NOT by itself require a spec/plan or a multi-agent (coder→reviewer→fix→re-review) pipeline — that is the ceremony-tier rule's call, scaled by size. Match the control to the risk, not to the roadmap.
- **Distinguish engineering from governance.** Code controls (this file's §1–§17) and governance/assurance (§19) are different work. Neither substitutes for the other. Automated tools produce the first, not the second.

---

## 1. Pre-code security checklist

Before writing feature code, answer:
- What trust boundary does this cross? (client→server, server→DB, server→third party, webhook→server, **untrusted-content→LLM, LLM→action**)
- What identity and authorization does it require? Where do they come from (never the client)?
- What tenant / user does the data belong to, and how is that enforced on *every* query?
- What untrusted input does it accept, and what's the schema?
- Does it send attacker-reachable content to an LLM, or let an LLM take actions? If so: what is trusted vs. untrusted in the prompt, and how is a tool call authorized to the caller's privilege (§6)?
- What secrets/credentials does it touch, and how are they stored?
- What's the failure mode — does it fail closed?
- What test proves the security property holds?

---

## 2. Authentication & sessions

- **[REQUIRED] Verify tokens server-side.** Never trust identity asserted by the client. Validate the session/JWT signature and expiry on the server (or via the auth provider) on every request.
- **[REQUIRED] Single auth chokepoint.** Route authentication through one middleware, not per-handler ad hoc checks. Missing/invalid credentials → 401.
- **[REQUIRED] Fail-closed identity derivation.** Derive `userId`/`tenantId` from the verified token, never from `req.body/query/params`. If identity is ambiguous (e.g., a user maps to 0 or 2+ tenants), deny — don't guess.
- **[REQUIRED] Credentials stored irreversibly.** Passwords hashed with a slow KDF (bcrypt/scrypt/argon2, appropriate cost). API keys and refresh tokens stored only as hashes; compared with constant-time equality; never logged.
- **[REQUIRED] Generate tokens from a CSPRNG.** Session ids, password-reset and email-verification tokens, API keys, and MFA backup codes come from a cryptographically secure RNG with ≥128 bits of entropy — never `Math.random`, a timestamp, or a predictable counter. Reset/verification tokens are single-use and short-TTL.
- **[REQUIRED] Rotate the session on privilege change.** Issue a fresh session id on login and on elevation (defeats session fixation); never reuse a pre-auth session id post-auth.
- **[REQUIRED] Plan for token revocation.** Stateless JWTs cannot be un-issued — this is their central weakness. Either keep access-token TTLs short (minutes) so revocation ≈ expiry, **or** maintain a server-side revocation/denylist or a token-version claim checked against server state — for logout-everywhere, compromise response, and role downgrades. "Logged out" must actually stop the token.
- **[REQUIRED] Multi-factor authentication (MFA).** Offer MFA to all users; **enforce it for admin/privileged/super-admin roles.** Prefer TOTP + passkeys/WebAuthn; avoid SMS. Enforce the assurance level **server-side** (read the `aal`/factor claim), not just in the UI. Gate sensitive actions (billing, member management, secret access, data export, **and API-key creation**) on the elevated level — an unscoped API key is an MFA bypass if it can be minted without MFA.
  - Recovery is part of the spec: hashed backup codes at enrollment; admin-resets-member; a verified path for the top-level account.
- **[REQUIRED] Harden third-party / OAuth / OIDC login.** Validate the `state` parameter (callback CSRF), allowlist `redirect_uri` exactly (no open redirect), use PKCE for public clients, and verify the ID token's signature, `iss`, `aud`, and expiry. Bind the IdP identity to your own account record — don't trust an unverified email claim to match accounts.
- **[RECOMMENDED] Enumeration resistance.** Constant-time login; identical responses for existent/non-existent accounts; always-success password-reset responses.
- **[RECOMMENDED] Session hygiene.** Short-lived access tokens; rotate refresh tokens on use; invalidate all sessions on password reset; bound timeouts on auth-provider calls so a hung dependency can't stall auth.
- **[VERIFY]** Tests: no-token → 401; tampered token → 401; expired token → 401; privileged route without MFA assurance → 403; API-key creation without MFA → 403; a revoked/logged-out token is rejected; post-login session id differs from pre-login; OAuth callback with a bad `state` or off-allowlist `redirect_uri` → rejected; generated tokens are unpredictable (CSPRNG, not `Math.random`).

---

## 3. Authorization & least privilege

- **[REQUIRED] Authorize every action, not just authenticate.** Check that *this* principal may perform *this* action on *this* resource. Authentication ≠ authorization.
- **[REQUIRED] Server-derived permissions.** Roles/scopes come from server state, never from the request.
- **[REQUIRED] Default-deny.** Unknown route/action/scope → denied. For machine credentials (API keys), enforce an explicit allowlist of permitted operations independent of granted scopes, plus a denylist for sensitive config paths.
- **[REQUIRED] Privileged/admin actions gated + logged.** Super-admin and cross-tenant operations require an explicit elevated check and produce an audit record (§13).
- **[RECOMMENDED] Reject path traversal / encoded segments** before routing.
- **[VERIFY]** Tests: principal without permission → 403; API key outside its allowlist → 403; horizontal privilege (user A acting on user B's resource) → denied.

---

## 4. Multi-tenant isolation (the highest-stakes control)

Cross-tenant leakage is usually the most damaging bug class in a SaaS. Treat it as load-bearing.

- **[REQUIRED] Scope every tenant query by the server-derived tenant id.** Every `select/update/delete/upsert` on a tenant table filters by `tenant_id`/`company_id` from the verified session — including lookups by primary key, joins/pre-filters, and bulk operations.
- **[REQUIRED] Re-verify caller-supplied foreign keys.** Any id/FK from the request must be confirmed to belong to the caller's tenant *before* use.
- **[REQUIRED] Scope child tables through their parent.** Tables without their own tenant column must join to a parent that has one (`!inner` + filter) or look the parent up and check ownership.
- **[REQUIRED] Never trust the client to assert its tenant.** No `tenant_id` from `body/query/params` for authorization.
- **[REQUIRED] A second layer — do not rely on one hand-written filter.** If your server uses a privileged DB connection that bypasses row-level security, the manual filter is the *only* gate. Add a backstop:
  - a **lint rule** that fails the build on any privileged query missing the tenant filter, **and/or**
  - a **tenant-scoped query wrapper** that injects the filter so it can't be forgotten, **and/or**
  - **database row-level security** enforced via a request-scoped role/claim (with explicit escalation for legitimate cross-tenant admin/cron/webhook work).
- **[VERIFY] Cross-tenant negative-test matrix — REQUIRED.** For every tenant route family, a test using Tenant A's token against Tenant B's ids must expect 403/404, never 200-with-data. This is the single most valuable security test suite you can own.

---

## 5. Input validation & output handling

- **[REQUIRED] Validate at the boundary with an explicit schema.** Parse and reject (e.g., zod/pydantic) — a defined shape, types, and bounds for every request. Reject malformed input with a 4xx; do not silently coerce or mutate.
- **[REQUIRED] Parameterized queries only.** Never build SQL/NoSQL/LDAP/shell strings by concatenation. Use bound parameters / prepared statements / an ORM's safe API.
- **[REQUIRED] Context-correct output encoding.** Encode for the sink (HTML, attribute, URL, SQL, shell). Prevent XSS via framework escaping and a Content-Security-Policy; sanitize rich HTML with an allowlist.
- **[REQUIRED] Bound resource use.** Cap body size, array lengths, string lengths, file sizes, and pagination to prevent DoS.
- **[RECOMMENDED]** A sanitizer is defense-in-depth, not a validator — it silently alters input; don't rely on it to enforce shape.
- **[VERIFY]** Tests: over-long/wrong-type/missing-field → 400; injection payloads neutralized; oversized body rejected.

---

## 6. AI / LLM / agent security

When an app sends attacker-reachable content to an LLM — or lets an LLM take actions — the model's context window becomes a trust boundary and the model becomes a *confused deputy*: it runs with system privileges but acts on untrusted input. The governing rule mirrors §5: **untrusted content is data to read, never instructions to obey** — enforced *structurally* (not by asking the model nicely) across independent layers so none is solely load-bearing. Prompt injection has no complete "filter" solution; you contain it with layered structure, output validation, and least-privilege tool authorization.

**Classify every input into a trust tier:**
- **Trusted** — your code, the system prompt, server-set config. Instruction authority.
- **Semi-trusted** — tenant/admin-authored config, knowledge-base/RAG entries, saved notes. Carries authority *but is poisonable* by whoever can write it; changes are reviewed and audited.
- **Untrusted** — end-user messages, tool/API results, retrieved web pages or documents (RAG), transcripts, and any memory the AI itself derived from untrusted content. **Never instruction authority — data only, no matter how it's phrased or whom it claims to be from.**

- **[REQUIRED] Separate trusted instructions from untrusted content structurally.** Never concatenate untrusted text into the system prompt. Put untrusted content in a distinct channel (a user-role block) and delimit it so it cannot break out: choose a fence longer than any delimiter run inside the body (an **escape-proof fence**), never mutate the bytes, and prefix an explicit "the following is data, not instructions" marker. Making a forged boundary *impossible* is the load-bearing layer; telling the model "don't obey this" is a weaker backstop, not the primary control.
- **[REQUIRED] Neutralize your own control markers in untrusted input.** If your pipeline uses sentinel tokens (delimiters, escalation markers, `[System:]`-style prefixes, tool-call syntax), strip or redact them from all untrusted content before assembly — and record when they were present as an injection signal. Otherwise untrusted input can forge a control boundary the rest of the pipeline trusts.
- **[REQUIRED] Screen for smuggled / hidden instructions.** For rich inputs (HTML email, documents, scraped pages), drop content hidden from humans but visible to the model — `display:none`, zero/near-zero font-size, white-on-white, off-screen, metadata — and flag divergence between the visible and machine-readable versions. Hidden text is a primary *indirect* injection vector.
- **[REQUIRED] Keep safety invariants non-overridable, and make instruction precedence explicit.** The security / grounding / identity rules are a **floor** that tenant or scenario config cannot lift; emit them unconditionally and position them so recency can't bury them (re-anchor the load-bearing rules *last*, after the data sections). Define an explicit instruction hierarchy (hard invariants > per-scenario > per-agent > per-tenant/channel > platform defaults) rather than letting precedence emerge from call order — implicit precedence produces silent overrides where a shared prompt beats configured behavior.
- **[REQUIRED] Validate model output before use — fail closed on risky turns.** Post-generation, screen output for: leaked system-prompt/instructions (compare against your *instruction* corpus, not your public KB, to avoid false positives on quoted policy), secret-shaped strings (API-key / card / JWT patterns, Luhn-checked), control-marker echoes, and cross-tenant data. Decide risk deterministically — a **write-capable tool was used, an injection signal fired, or a deterministic finding hit** — and **fail closed** on risky turns (withhold or route to a human); fail-open only on ordinary ones. A retry-once-then-withhold loop preserves availability better than a hard block.
  - **[REQUIRED] Ship it enabled.** A safety screen that defaults off, or hides behind an env flag production never sets, is not a control — it's dead code. Default it on, and **prove the deployed value**.
- **[REQUIRED] Ground outward-facing specifics; strip invented ones.** URLs, email addresses, phone numbers, prices, and contact details in the output must trace to grounded sources (`system prompt ∪ transcript ∪ tool results`); strip anything the model invented. This is the concrete defense against exfiltration-via-fabricated-URL and hallucinated contact details. Caveat: a URL that arrived via a *poisoned* KB/tool result counts as "grounded" — pair grounding with source trust-tiering, don't rely on it alone.
- **[REQUIRED] Authorize tools/actions at the caller's privilege, not the agent's.** The agent is a confused deputy — authorize every tool call against the end-user/tenant who owns the request, tenant-scoped like any other query (§4), never against the agent's ambient system privileges. Maintain a **closed allowlist of read-only tools; treat every unknown or tenant-defined tool as write-capable** (guessing read-only is the unsafe direction). Tools set to "off" are never exposed to the model.
- **[REQUIRED] Human approval for write / irreversible / high-impact actions.** A write tool invoked from an AI turn does not execute silently: default the write policy to "ask" — enqueue a pending action for human approval and instruct the model *not* to claim it's done — and reserve "auto" for explicitly configured, bounded, reversible operations. Untrusted content must never trigger a side effect on its own.
- **[REQUIRED] Never trust model output as authority for security- or money-relevant values.** Any amount, id, permission, price, or recipient the model emits is prompt-injection-influenceable. Re-validate it against server-side truth before acting (e.g., cap a refund at the amount actually collected per the ledger; confirm a target id belongs to the caller's tenant). Apply this to **every** such value — don't generalize "we capped the refund" into "the model's numbers are safe."
- **[REQUIRED] Isolate and minimize the context.** Only this tenant's and this session's data enters the prompt (§4); redact secret-shaped values before they reach the provider *and* on the way out (injection can surface a secret in rewritten output). Never place another tenant's or another user's data within reach. Data sent to a third-party model API has left your boundary — §11 minimization applies.
- **[REQUIRED] Guard model-initiated fetches with the SSRF rules (§15).** `read_url`-style tools re-validate every redirect hop, pin the validated IP (DNS-rebinding defense), and reject non-http(s) schemes — an agent that fetches attacker-chosen URLs is a first-class SSRF surface.
- **[REQUIRED] Treat AI config and prompts as security-relevant, tenant-scoped state.** System prompts, model/version pins, and few-shot examples are load-bearing config: changes are reviewed and audited (§17). Per-tenant AI config carries a tenant id — a single global prompt row is a one-edit, all-tenant blast radius. Retrieval/RAG corpora are semi-trusted: a poisoned document is an injection vector even though it's "your own data."
- **[REQUIRED] Never render AI output as trusted markup.** Model output shown in a browser is untrusted for XSS purposes — sanitize with an allowlist or render as inert text / markdown-safe-by-construction; never `dangerouslySetInnerHTML` raw model output. If AI output ever feeds a query, URL, or command, the §5 parameterization/encoding rules apply to it too.
- **[RECOMMENDED] Log the AI security signals.** Persist injection signals, screen decisions, and tool invocations (with actor/tenant) to the audit trail (§13) — you cannot investigate an injection incident you never recorded.
- **[RECOMMENDED] Rate-limit and cost-bound model calls per tenant** (§8) — LLM calls are a denial-of-wallet vector.
- **[VERIFY] Adversarial red-team suite — REQUIRED for any AI surface that ingests untrusted content.** Drive *real* payloads through the actual prompt-assembly and screening functions (not mocks) and assert: a direct override ("ignore previous instructions") is treated as data; a fence-escape payload cannot break out of its block; a preamble/marker mimic cannot forge a boundary; spoofed `[System:]`/control markers are neutralized; hidden text (white-on-white / `display:none`) is dropped and flagged; a poisoned KB / tool-result / retrieved page does not change behavior; a divergent or injected turn makes the output screen fail **closed**; output reciting the system prompt or containing a secret is withheld; and — the false-positive guard — quoting your *own* KB policy is **not** flagged. Also assert the live send path actually withholds in its **production** screening configuration.

---

## 7. Browser & session security (CSRF, CORS, headers)

Applies to any surface a browser reaches with **ambient credentials** (cookies). Pure bearer-token-in-`Authorization` APIs are largely CSRF-immune but still need CORS and header hygiene.

- **[REQUIRED] CSRF protection for cookie-authenticated state changes.** If sessions ride on cookies, every state-changing request (POST/PUT/PATCH/DELETE) needs an anti-forgery proof: `SameSite=Lax|Strict` on the session cookie **plus** a synchronizer/double-submit token (or a strict `Origin` / `Sec-Fetch-Site` check for JSON APIs). SameSite alone is defense-in-depth, not the whole control. Bearer-token APIs are exempt — state that exemption explicitly rather than leaving it implied.
- **[REQUIRED] Explicit, non-reflecting CORS.** Allowlist trusted origins. **Never** reflect the request `Origin` back together with `Access-Control-Allow-Credentials: true` — that combination is a full credential-theft hole. Default to same-origin; open specific origins deliberately.
- **[REQUIRED] Security response headers.** Ship at minimum: `Strict-Transport-Security` (long max-age + preload), `Content-Security-Policy` (§5), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` / `frame-ancestors 'none'` (clickjacking — required for any authenticated app), and a restrictive `Referrer-Policy`. Session cookies flagged `Secure`, `HttpOnly`, and scoped `SameSite`.
- **[RECOMMENDED]** `Cross-Origin-Opener-Policy` / `Cross-Origin-Resource-Policy` for high-value apps; keep CSRF tokens even on bearer flows if any cookie fallback path exists.
- **[VERIFY]** Tests: a state-change with missing/invalid CSRF token → rejected; a forged cross-origin form POST is inert; responses carry HSTS + `frame-ancestors` + `nosniff`; CORS does not reflect an arbitrary `Origin` with credentials.

---

## 8. Abuse resistance & rate limiting

§5 caps a *single* request's size; this caps *frequency across* requests.

- **[REQUIRED] Throttle authentication and abuse-prone endpoints.** Rate-limit by IP **and** by account on login, password-reset, MFA-verify, token mint, signup, and any enumerable or expensive operation. Progressive backoff / temporary lockout after repeated failures, with a verified unlock path (not time-only while credential-stuffing is active).
- **[REQUIRED] Bound cost, not just count.** Per-tenant quotas on expensive operations (exports, report / AI generation, outbound email) to prevent denial-of-wallet and resource exhaustion — so one tenant can't starve the others. A global limit alone doesn't isolate blast radius.
- **[RECOMMENDED]** Bot mitigation (CAPTCHA / attestation / proof-of-work) on signup and password-reset once abuse is observed; separate per-user from per-IP limits to survive shared NAT/proxies.
- **[VERIFY]** Tests: N rapid failed logins → throttled/locked, not unlimited; a burst on an expensive endpoint is shed; a per-tenant limit protects neighbors.

---

## 9. Webhooks & external callbacks

- **[REQUIRED] Verify a per-sender cryptographic secret BEFORE any DB access or side effect.** HMAC signature (constant-time compare) over the raw body, provider JWT (e.g., OIDC), or equivalent. Reject before doing anything else.
- **[REQUIRED] Resolve identity from the verified payload, never from an unverified identifier.** Do not look up a tenant by a caller-supplied phone number / shop domain / customer id and then act — the identifier must be bound to the sender by the signature.
- **[REQUIRED] Replay protection.** Enforce a timestamp window and idempotency/dedup on event ids.
- **[REQUIRED] Fail closed.** No secret configured / verification fails → reject (in production, don't silently accept). Don't persist raw payloads before verification (log-poisoning).
- **[RECOMMENDED]** Rate-limit public webhook endpoints (§8); process only for enabled/active integrations.
- **[VERIFY]** Tests: bad signature → rejected; replayed timestamp → rejected; valid signature but mismatched tenant → rejected.

---

## 10. Secrets & key management

- **[REQUIRED] No secrets in the repo.** `.env*` gitignored; only `.example` placeholders committed. Scan history and CI for leaked secrets (§17).
- **[REQUIRED] Server-only privileged keys.** Service-role / admin keys never reach the client bundle; client env exposes only public values.
- **[REQUIRED] Encrypt secrets at rest with authenticated encryption.** AES-256-GCM (random IV, auth tag) or a managed vault for stored third-party credentials/tokens. Separate keys for separate data classes (independent blast radius).
- **[REQUIRED] Validate config at boot.** Fail fast on missing/malformed required secrets; never silently derive one security secret from another.
- **[REQUIRED] Rotation readiness.** Ciphertext/records must carry a **key version** so keys can be rotated cleanly after a suspected leak or a departing privileged employee. You will need this exactly when you're least able to build it.
- **[RECOMMENDED] Proportionate key infrastructure.** A managed KMS and per-tenant keys are warranted for regulated/high-value data (PHI, stored card data, custody). For general consumer PII, env-injected keys from a managed secret store + rotation are an accepted baseline — size to the data class and record the decision.
- **[VERIFY]** No secret in `git ls-files` / history; boot fails without a required key; a rotation runbook exists and is tested on a sample.

---

## 11. Data protection & privacy

- **[REQUIRED] Encryption in transit.** TLS everywhere (client↔server, server↔DB, server↔third parties). Never disable cert verification.
- **[REQUIRED] Encryption at rest.** Enable database/disk at-rest encryption.
- **[REQUIRED] Data minimization.** Collect and retain only what's needed. Don't log PII/secrets; redact in logs and error traces; be deliberate about what leaves your system (including what's sent to third-party/AI APIs — see §6).
- **[REQUIRED] Deletion & subject rights.** Support data-subject deletion/export (GDPR/CCPA apply to consumer PII regardless of industry). Ensure "delete" actually deletes (including backups policy and derived copies), and that compliance/redaction webhooks don't silently no-op.
- **[RECOMMENDED] Field-level encryption for the most sensitive fields** (PHI, government ids, payment data, credentials). For general PII, access control + at-rest + tenant scoping is the common bar; field-level is optional hardening. _Note: encrypting PII can grant breach-notification safe harbor under many US state laws — factor that into the decision._
- **[RECOMMENDED] Retention policy.** Define and enforce retention/TTL for messages, logs, and PII.
- **[VERIFY]** A deletion request removes the data across primary + derived stores; logs contain no plaintext secrets/PII; a redaction webhook actually redacts.

---

## 12. Database & row-level security

- **[REQUIRED] Enable row-level security on tenant tables** (defense in depth behind the app layer).
- **[REQUIRED] Symmetric UPDATE/INSERT policies.** UPDATE needs both `USING` and `WITH CHECK`; INSERT needs `WITH CHECK` — otherwise rows can be mutated into or inserted for another tenant.
- **[REQUIRED] Harden privilege-elevating functions.** For `SECURITY DEFINER` / stored procedures that bypass RLS:
  - pin a safe `search_path`;
  - take an explicit tenant/owner parameter and validate ownership in the body;
  - **revoke execute from PUBLIC** (and anon/authenticated) if server-only — revoking from the two roles alone is insufficient while PUBLIC retains the grant;
  - if callable by clients, use a caller-is-member check that still lets trusted server callers (no end-user identity) through — fail closed for clients, open for the server, never the inverse.
- **[REQUIRED] Lock sensitive tables to service-role only** (RLS enabled, no policies, grants revoked from anon/authenticated).
- **[REQUIRED] Storage/bucket policies specify an explicit role** and bind writes to the owner's folder/tenant; a missing role clause defaults to public.
- **[VERIFY] RLS policy tests (e.g., pgTAP) — REQUIRED.** Assert cross-tenant denial, fail-closed helpers, hardened-function rejection, and sensitive-table lockdown. After locking down a function, query the live grants to confirm anon/authenticated cannot execute it.

---

## 13. Logging, audit & monitoring

- **[REQUIRED] Audit trail of security-relevant actions.** Record who did what, when, from where: authentication events, permission/role changes, privileged/admin and super-admin actions, data exports, config changes, and AI tool invocations / injection signals (§6). Capture actor, action, target, timestamp, and source IP.
- **[REQUIRED] Protect the audit log.** Append-only intent; not readable by ordinary tenants (service-role/admin only); retained per policy. Super-admin actions especially must be logged — the most privileged actor needs the most scrutiny.
- **[REQUIRED] Reliable audit writes on the critical path.** Don't make security audit logging silently fire-and-forget — surface/alert on write failure (see §14).
- **[REQUIRED] Neutralize log injection.** User-controlled values written to logs are encoded/escaped so they can't forge log lines (CRLF injection) or corrupt structured-log parsing. Prefer structured logging with fields over string interpolation.
- **[REQUIRED] Keep secrets/PII out of URLs.** Tokens, session ids, and PII never travel in query strings or path segments — they land in access logs, proxies, browser history, and `Referer` headers. Use headers or request bodies.
- **[RECOMMENDED] Alerting.** Alert on auth anomalies, authorization-failure spikes, and security-control failures.
- **[VERIFY]** A privileged action produces an audit row with actor + IP + timestamp; ordinary tenants cannot read the audit table; an injected log value cannot forge a second log line.

---

## 14. Error handling (fail closed, surface failures)

- **[REQUIRED] Deny on error.** If an auth/authorization/ownership check errors, deny. Never let an exception fall through to allow.
- **[REQUIRED] Don't swallow security-relevant errors.** A DB/query call whose result you branch on must handle its error path. The pattern `const { data } = await q; if (!data) return;` silently swallows a permission denial or a schema error that returns `{ data: null, error }` without throwing — bind and surface the `error` first. (A lint rule can enforce this; it has prevented real outages.)
- **[REQUIRED] Safe error responses.** No stack traces, SQL, secrets, or internal hostnames in responses. Log details server-side; return a generic message + correlation id.
- **[VERIFY]** Inject a dependency failure on an auth/ownership path → request is denied, not allowed; error responses contain no sensitive internals.

---

## 15. SSRF & outbound requests

- **[REQUIRED] Guard server-side fetches of user/tenant-supplied URLs.** Reject private, loopback, link-local, and cloud-metadata ranges — including IPv4-mapped/compatible IPv6, 6to4, and NAT64 forms (e.g. `::ffff:169.254.169.254`). This includes URLs an LLM tool is asked to fetch (§6).
- **[REQUIRED] Prevent DNS rebinding / TOCTOU.** Pin the validated IP for the actual connection rather than re-resolving; apply the guard on every hop (redirects, pagination, token endpoints, importers, proxies).
- **[REQUIRED] Don't forward credentials cross-origin.** On redirects and `Link: next` pagination, enforce same-origin or strip auth headers before following an upstream-chosen host.
- **[VERIFY]** A request to a metadata/loopback address (including IPv4-mapped IPv6) is blocked end-to-end; a cross-origin redirect doesn't leak the auth header.

---

## 16. Concurrency & business-logic integrity

Injection isn't the only way to break logic — **timing and replay** are.

- **[REQUIRED] Idempotency on money and irreversible actions.** Beyond webhooks (§9): payments, credit grants, coupon redemption, and "send once" actions carry an idempotency key or a unique constraint so a retried/double-submitted request applies exactly once.
- **[REQUIRED] Guard read-modify-write races.** Balance/inventory/quota mutations use atomic DB operations, `SELECT … FOR UPDATE`, optimistic-concurrency versions, or a DB-level constraint — never read-then-write in app code across a network hop. A TOCTOU ownership/limit check must be re-verified *inside* the transaction that acts on it.
- **[VERIFY]** Tests: two concurrent redemptions of a one-use coupon → exactly one succeeds; a double-submitted payment charges once; a race can't push a quota past its cap.

---

## 17. CI/CD & supply chain (automated gates)

Make the machine enforce what humans forget. **[REQUIRED] in CI:**
- **Secret scanning** (e.g., gitleaks) — block new secrets; scan history.
- **Dependency / CVE scanning** for *application* dependencies (e.g., Dependabot/npm audit/renovate) — not just CI-action updates.
- **SAST** (e.g., CodeQL) on PRs + a schedule.
- **Typecheck + build** must pass to merge.
- **Tests must gate merges/deploys.** Non-blocking tests on an auto-deploying branch means a red test reaches prod — fix flakes, don't disable the gate.
- **The security lint rules** from §4 (tenant scope) and §14 (swallowed errors) run and block.
- **[RECOMMENDED]** Pin/refresh a single lockfile; require review for dependency additions; sign commits/artifacts where feasible. For AI surfaces, run the §6 adversarial injection battery as a scheduled/PR gate.
- **[VERIFY]** A planted secret trips the scanner; a known-vuln dependency is flagged; a deliberately unscoped tenant query fails lint.

---

## 18. Security review checklist (before merge)

For any change touching auth, routes, RPCs/stored procedures, RLS, webhooks, crypto, AI prompt/tool wiring, or data handling, confirm:
- [ ] Every tenant query is scoped by the server-derived tenant id (or joins through a parent that is).
- [ ] No `tenant_id`/`user_id` for authorization is read from the request.
- [ ] Caller-supplied FKs are re-verified against the caller's tenant.
- [ ] New input is validated against an explicit schema; errors return 4xx (no silent coercion).
- [ ] New webhooks verify a per-sender secret before any side effect; replay-protected; fail closed.
- [ ] New privileged functions pin `search_path`, validate ownership, and revoke execute from PUBLIC/anon/authenticated (or use the fail-closed-for-clients caller check).
- [ ] New UPDATE/INSERT policies have both `USING` and `WITH CHECK`.
- [ ] Secrets aren't logged or committed; new keys are server-only and versioned; tokens come from a CSPRNG.
- [ ] Cookie-auth state changes are CSRF-protected; CORS doesn't reflect arbitrary origins with credentials; security headers present (HSTS, `frame-ancestors`, `nosniff`, CSP).
- [ ] Auth/abuse-prone endpoints are rate-limited / lockout-protected; sessions rotate on auth and a revocation path works; OAuth flows validate `state`, `redirect_uri`, PKCE, and token claims.
- [ ] Money/irreversible actions are idempotent and race-safe.
- [ ] **AI:** untrusted content reaches the model as delimited data (never the system prompt), control markers neutralized, hidden text stripped; safety invariants non-overridable and anchored last with explicit precedence; output screened (leak/secret/cross-tenant) and fails closed on risky turns, **enabled in prod**; tool calls authorized at the caller's privilege, writes gated on human approval, model-emitted money/ids re-validated server-side; model output never rendered as trusted markup.
- [ ] Security-relevant errors are surfaced, not swallowed; the path fails closed on error.
- [ ] Privileged/admin/super-admin actions are audit-logged; logs neutralize injected input; no secrets/PII in URLs.
- [ ] Outbound fetches of supplied URLs (including LLM-tool fetches) pass the SSRF guard.
- [ ] Tests added: cross-tenant negative case, authz denial, the specific failure mode, and (for AI surfaces) the adversarial injection cases.
- [ ] Adversarial check: how would an attacker abuse this change? Is there a second layer if the first fails?

---

## 19. Governance & assurance (human-owned — not produced by tooling)

Automated tools and AI produce code controls (§1–§17); they do **not** produce these. A human must own them:
- **[REQUIRED] Named security owner** — a person accountable for the program.
- **[REQUIRED] Written security program** — policies, standards, and a maintained risk register.
- **[REQUIRED] Incident-response plan** — roles, severities, communication, and runbooks; rehearsed.
- **[REQUIRED] Backup & recovery assurance** — documented RPO/RTO and a *tested* restore (including single-tenant restore where multi-tenant).
- **[REQUIRED] Independent penetration test** — periodic third-party testing; internal reviews don't substitute. Triage findings with owners and dates.
- **[REQUIRED] Compliance applicability review** — determine which frameworks actually apply (e.g., GDPR/CCPA for consumer PII; FTC Safeguards / NY DFS 500 for covered financial entities; HIPAA only with PHI) and map obligations to controls. Don't accept obligations you don't have; don't ignore ones you do.
- **[RECOMMENDED] Framework scoring** — score against a real standard (SOC 2 Common Criteria, OWASP ASVS, NIST CSF) to replace estimates with "X of Y controls met" and evidence.
- **[RECOMMENDED] Vendor/subprocessor review, security training, access reviews** — periodic. Include AI/model providers in the subprocessor review (data leaves your boundary when it reaches them).

---

## 20. Security definition of done

A feature/app is "secure-done" when:
1. Its trust boundaries are identified and each is authenticated + authorized server-side.
2. Every tenant/user data path is scoped and has a second layer (or a lint/RLS backstop).
3. All input is validated; all output encoded; all queries parameterized.
4. Secrets are stored safely, versioned, and out of the repo; keys are rotatable.
5. It fails closed, and security-relevant errors are surfaced.
6. Privileged actions are audit-logged.
7. Browser surfaces are CSRF-safe, security-headered, and rate-limited; money/irreversible paths are idempotent and race-safe.
8. Any AI surface enforces the untrusted-content-is-data boundary structurally, screens output fail-closed **in production**, authorizes tool actions at the caller's privilege, and has an adversarial injection test suite.
9. Tests prove the security properties (incl. a cross-tenant negative case) and gate CI.
10. Security scanning (secrets/deps/SAST) is green in CI.
11. Any risk-acceptance is explicit, justified, and owned by a human.
12. The §18 review checklist passes.

> **Remember:** the goal is to be both *actually secure* (the code does the right thing) and *provably secure* (tests, scans, and governance demonstrate it and keep it that way). Shipping one without the other is an incomplete security posture.

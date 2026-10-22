# Project Profit Tracker

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

FastAPI and PostgreSQL for the backend, confirmed by the owner on 2026-10-11.
Next.js and TypeScript for the frontend, based on the preceding recommendation.
The owner explicitly rejected SQLite and a local-only product.
The web and API are separate deployment units in one standalone product repository.
Target placement follows the existing hosting decision: Vercel for the web,
Railway for the API, and a dedicated PostgreSQL database such as Neon.
Provider provisioning and production deployment still require approval and configuration.

## Users

Initial audience: independent freelancers and small agencies who need to know
whether a client project pays for the time and expenses it consumes.
This audience is the proposed MVP scope from the conversation, not validated
market demand.

## Product Purpose

Record a project's contract value, actual payments, work time, hourly labor
cost, and direct expenses. Make actual profit and projected profit distinguishable.
A successful MVP lets an authenticated user create a workspace and project,
record entries, and see a reproducible financial result after reloading.

## Positioning

A focused project profitability workflow, not another general CRM and not
a replacement accounting or tax system. The important output is what the
project actually earned after labor and expenses, rather than revenue alone.

## Operating Context

Users work across several client projects, record small increments of time,
and compare completed and ongoing work. Each project uses one currency.
Workspace membership controls access; users do not gain access by supplying
another workspace identifier.

## Capabilities and Constraints

- Project creation and editing, including contract value and default hourly labor cost.
- Time entries with an hourly-cost snapshot, expenses, and actual payments.
- Actual profit, projected profit, cash surplus, and profit per worked hour.
- Currency-specific summaries; currencies are never silently added together.
- Integer minor-unit money throughout, with explicit rounding for labor cost.
- PostgreSQL migrations, bounded connection pools, stateless authenticated API instances.
- OIDC authentication and server-side workspace authorization; no production auth bypass.
- Explicit loading, empty, error, and authentication/configuration states.
- No production Solo Empire credentials, financial records, or direct cross-cell database access.
- No subscription billing, tax filing, invoicing integration, or automatic CRM/time-cell import in this MVP.

## Evidence on Hand

The repository is a new product boundary. It has no real customer data,
testimonials, verified commercial results, or approved pricing.
Any test or demonstration data must be identified as synthetic.
Hosting targets are documented intentions, not proof of a live deployment.
The API has opt-in Sentry error reporting (off unless `SENTRY_DSN` is set; allowlist
scrubber, errors only); no Sentry project or DSN is configured yet, and the web BFF
has no error reporting.

## Product Principles

- Show the calculation and its inputs, not only a persuasive headline number.
- Distinguish money received from contract value and cash from profit.
- A missing result is not zero; show what input or configuration is missing.
- Keep tenant data isolated and retain the product's independence from the hub.
- Treat tests and deployment readiness as evidence, not a claim of proven production scale.

## Accessibility & Inclusion

Thai-first interface with accurate technical identifiers and currency formatting.
Keyboard-operable forms, visible focus, associated labels, readable errors,
and responsive task completion on desktop and mobile.

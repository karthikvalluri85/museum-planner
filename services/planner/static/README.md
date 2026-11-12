# Planner web frontend

Plain HTML, CSS, and one ES module. No build step, no dependencies.

- `index.html`: page shell and the form controls
- `app.js`: api client, pure helpers (exported for tests), rendering, event wiring
- `styles.css`: calm light and dark themes, 44px tap targets, reduced-motion safe
- `favicon.svg`

Preview with the contract mock from the repo root: `node tools/mock-planner.mjs 8099`, then open http://localhost:8099. Put `nostepfree` or `nothing` in the interests box to see the other two response shapes.

Guardrails the UI keeps:

1. The "Step-free route" banner shows only when the response says `plan.step_free === true`, never because it was requested.
2. On `no_step_free_route` it says plainly that no step-free plan exists and lists the alternative under a heading that says it uses stairs, naming every staircase.
3. Gallery numbers are printed exactly as received, after the word "Gallery".
4. The notices footer (public data, not reviewed by the museum) is always visible on a plan and cannot be dismissed; floor-data verification status also shows on the form.
5. API data is only ever written with `textContent`, never `innerHTML`.

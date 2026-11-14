# Security

## What this project holds

- No secrets. The museum APIs need no keys, and the planner is public by design.
- No visitor data. The planner keeps no accounts, cookies, or request bodies.
- Cloud credentials are never stored in the repository. Deployment from GitHub uses Workload Identity Federation, not service-account keys.

## Reporting a problem

Please open a private security advisory on this repository (Security tab, then "Report a vulnerability") rather than a public issue.

The report that matters most here is not a classic vulnerability: it is a route labeled step-free that is not. If you find one, say which museum, which galleries, and what is actually there.

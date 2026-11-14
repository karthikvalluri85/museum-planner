# Launch deliverables

Drafted 2026-11-13, before anything was deployed. Due 2026-11-18. Launch 2026-11-20.

| File | What it is |
|---|---|
| `tagline.md` | Five candidates under 60 characters |
| `description.md` | The Product Hunt description |
| `architecture.svg` | One diagram, five boxes |
| `demo-script.md` | 30-second screen recording script |
| `faq.md` | Six questions with honest answers |

## Before publishing: every claim below must be true on the day

The drafts are written as the product is designed to be. Check each against the deployed app and edit the copy, not the truth.

- [ ] The app is deployed and `deploy/verify.sh <url>` passes against it.
- [ ] Both museums ingest from the live APIs and return plans. If only the Met works, change "Two museums today" in `description.md` and `faq.md`.
- [ ] A 90-minute step-free request at the Met returns `status: ok`. If it returns `no_step_free_route` for common interests, fix the floor graph or change the demo.
- [ ] The floor-graph verification status in the app matches what `description.md` and `faq.md` say about it. If someone has checked it against the plan or on site, say that instead.
- [ ] "Takes about a day" per floor graph (FAQ 3) matches how long it actually took.
- [ ] The Smartify and Infiniteach descriptions are still accurate. Reread their sites the day before.
- [ ] "No accounts and no cookies" (FAQ 6) is still true of what is deployed, including any analytics added later.
- [ ] No file in the repo contains the word the original brief bans. Check with a case-insensitive search for `personaliz`.
- [x] Open-source answer decided: MIT, repository public (2026-11-14).

## November 17

Build the Product Hunt page, schedule it for November 20, and tick the contest opt-in in the launch scheduler.

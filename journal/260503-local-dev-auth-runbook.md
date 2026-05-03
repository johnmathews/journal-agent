# Local-dev auth runbook

Documented the register-then-verify flow in `docs/development.md`. SMTP
isn't wired up locally and the auth middleware blocks every protected
route on `email_verified=true`, so the previous "Local Development (Full
Stack)" section sent fresh contributors into a 403 wall the moment they
tried to log in. The new "Creating a local test user" subsection shows
the curl + sqlite UPDATE flow and points at the matching webapp doc for
the full-stack quickstart and fake-jobs seeder.

No code changes here — only a docs cross-link. The companion webapp
commit (`journal-webapp/journal/260503-job-history-tweaks-and-local-dev-docs.md`)
holds the substantive runbook.

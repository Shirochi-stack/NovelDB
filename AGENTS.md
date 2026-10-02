# Repository naming

Use source codenames everywhere: `npia`, `kpage`, `sfc`, `nweb`, `mpia`,
`jara`, `rbooks`, `nseries`, and `floo`. This applies to paths, identifiers, docs,
comments, UI text, workflow labels, commit messages, release titles/notes,
and release asset names/labels.

Keep external wire contracts working. Store indispensable addresses,
selectors, cookie names, and native application identifiers with Unicode
escapes. Use `scripts.source_names.dump`/`dumps` for generated JSON so
provider text cannot reappear in stored files. This convention does not
hide the connected services from someone inspecting runtime traffic.

Run `python scripts/codename_policy.py check --commits` before publishing.
After staging metadata, run `python scripts/codename_policy.py sanitize-data`
before committing. The publication helper applies this automatically.
After rebuilding upstream scraping rules, run
`python scripts/codename_policy.py sanitize-rules rules-lib.js`.
Use the same codename policy when creating or editing GitHub releases.

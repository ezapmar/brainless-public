---
owner_name: the owner
output_lang: en
company_area: Work
worker_name: worker
owner_full_name: the owner
private_segments: 
protected_homes: Personal/Official Docs, Thinking/_local
gitignore_nets: **/Official Docs/, **/* - Health/, **/_local/, **/Security Incidents/
---

# Owner profile

Read by tools/owner_profile.py. owner_name and output_lang (tr or en) shape every prompt; company_area is the folder under Work/ the compiler treats as your company; worker_name is the commit suffix of an always-on machine, if you run one; private_segments lists extra folder names that must never be compiled.

protected_homes lists the folders that must never reach the remote, and gitignore_nets the .gitignore pattern rules that back up each rule naming a single path. tools/health_check.py reports on them and tools/tests/test_gitignore_guards.py asserts git really ignores them, so add a home here and both follow. Put the folders holding health records, identity documents, finance exports or anything about a child in this list before your first commit: .gitignore does not untrack, so a file committed once needs history surgery to remove.

## Cross-link rule

CRITICAL CROSS-LINK RULE: personal and work effects must be surfaced. Whenever a personal topic affects work or the reverse, link both ways.

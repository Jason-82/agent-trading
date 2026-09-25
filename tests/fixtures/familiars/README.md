# familiars.family fixtures

**synthetic: true** — every file in this directory was hand-built to the shapes documented
in `docs/familiars-api.md`. Nothing here was recorded from the real service; handles,
wallets, signatures and keys are made up (`fam_test_...` is a placeholder, not a key).
`tools/record_fixtures.py` (WP-F) writes recorded, secret-redacted copies to
`tests/fixtures/recorded/` and `tests/test_fixture_schema.py` parses both sets with the
same pydantic models.

| file | endpoint |
|---|---|
| challenge.json | POST /api/agents/challenge |
| register.json | POST /api/agents/register |
| me.json / me_pause.json | GET /api/agent/me (owner settings; `me_pause` carries a pause directive) |
| agents_7d.json / agents_30d.json | GET /api/agents?range=7D / 30D |
| agent_detail.json | GET /api/agents/{handle} |
| tokens.json / tokens_burst.json | GET /api/tokens (burst = one mint gained many agents) |
| post_ok.json / post_429.json | POST /api/posts (accepted / rate limited body) |
| owner_key.json | POST /api/agent/owner-key |

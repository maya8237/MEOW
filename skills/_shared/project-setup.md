# Project ignore boundary

Apply this before onboarding or migration writes MEOW state. Each caller owns
repair and verification; do not assume another flow already applied it.
Preserve unrelated rules and remove obsolete broad MEOW rules when safe.

```gitignore
.meow/*
!.meow/
!.meow/config.toml
!.meow/skills/
!.meow/plugin/
!.meow/.claude-plugin/
!.meow/agents/
!.meow/workflows/
.claude/settings.local.json
```

Verify with `git check-ignore`: shared `.meow/config.toml` must be trackable;
`.meow/config.local.toml` and a sample runtime path must be ignored. A single
`.meow/` rule hides shared configuration and is insufficient.

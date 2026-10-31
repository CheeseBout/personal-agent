# ADR-0004: Pluggable web search, keyless default

- Status: accepted (Phase 0)

## Decision
SearchProvider interface; providers: DuckDuckGo (keyless default), Tavily, Brave, SearXNG.

## Why
Works on first run without a key; better providers are a config change.

## Consequences
Keyless scraping can break; the package and version for the DDG provider are pinned in Phase 1, not guessed now.

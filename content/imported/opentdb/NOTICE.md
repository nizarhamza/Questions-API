# OpenTDB import

The `.jsonl` shards in this directory are imported from the
**Open Trivia Database** (<https://opentdb.com>) via `python -m qbank import
--source opentdb`.

## Licence

OpenTDB content is licensed **CC BY-SA 4.0**
(<https://creativecommons.org/licenses/by-sa/4.0/>). That licence travels with
these questions:

- **Attribution** — credit the Open Trivia Database.
- **ShareAlike** — if you redistribute these questions or adaptations of them,
  do so under CC BY-SA 4.0.

This is different from the rest of `content/`, which is derived from Wikidata
(CC0) and carries no such obligation. The generator code remains MIT.

Each record's `e` field also names the source, so attribution survives even
when a single question is served in isolation.

---
name: library-status
description: Snapshot of the vibe DB and taste loop — track/section counts by source, tagged vs propagated coverage, pending reviews, recent sets. Use for "how's my library", "library status", "what's tagged", "do I have enough for a set".
---

One query pass, one compact report. Needs `DATABASE_URL` (say so and stop if unset).

```bash
uv run python -c "
import json
from dj.vibe import store
from dj import persist
conn = store._connect()
cur = conn.cursor()
q = lambda sql: (cur.execute(sql), cur.fetchall())[1]
print(json.dumps({
  'tracks_by_source': q(\"SELECT source, count(*) FROM tracks GROUP BY source\"),
  'taste': q(\"SELECT COALESCE(taste_source,'untagged'), count(*) FROM tracks GROUP BY 1\"),
  'favorites': q('SELECT count(*) FROM tracks WHERE is_favorite'),
  'sections': q('SELECT count(*) FROM sections'),
  'bpm_spread': q('SELECT min(bpm)::int, percentile_disc(0.5) WITHIN GROUP (ORDER BY bpm)::int, max(bpm)::int FROM tracks'),
  'pending_reviews': q(\"SELECT status, count(*) FROM pending_taste GROUP BY status\"),
}, default=str))
conn.close()
hist = persist.load_history()
print('recent sets:', [(h.get('brief'), h.get('approved')) for h in hist[-3:]])
"
```

Report as a short table + one or two pointed suggestions, e.g.:
- manual tags < 50 → "tag ~N more (`/bulk-judge` a playlist you love, or
  `uv run python -m dj.taste.tag`), then `--propagate`"
- pending reviews waiting → "ingest their files and they apply themselves"
- tracks but no sections → segmentation fell back hard; worth a look.
- narrow BPM spread vs the brief George wants → feed the library that tempo.

---
name: rekordbox-validator
description: Validate a generated rekordbox.xml + m3u8 against the export invariants (well-formed XML, resolvable locations, sane cue marks, playlist keys, no TEMPO element). Use after touching dj/export or before George imports a set into rekordbox.
tools: Bash, Read, Glob
---

You check the manual-mode artifacts (`renders/*.rekordbox.xml`, `renders/*.m3u8`,
or a path you're given) against what `src/dj/export/rekordbox.py` promises
(ADR 0010). Read that module first if anything looks ambiguous — the code is the
spec.

Checks, in order (one python snippet is fine; report each as PASS/FAIL + evidence):

1. **Well-formed**: `xml.etree.ElementTree.parse()` succeeds; root is
   `DJ_PLAYLISTS Version="1.0.0"` with PRODUCT + COLLECTION + PLAYLISTS.
2. **Collection integrity**: `COLLECTION@Entries` == number of TRACK children;
   TrackIDs unique; every TRACK has Name, AverageBpm parseable as float,
   Tonality non-empty, TotalTime integer.
3. **Locations**: each `Location` starts `file://localhost/`, URL-unquotes to an
   absolute path; flag (don't fail) paths that don't exist on disk — the plan
   may predate a library move, but George should know before importing.
4. **No TEMPO element** on any TRACK — deliberately omitted (a 0.000-anchored
   grid would be confidently wrong; rekordbox must analyze). Its presence means
   a regression.
5. **Cue marks**: POSITION_MARKs come in hot+memory pairs (Num 0/-1 for MIX IN,
   1/-1 for MIX OUT); Start ≥ 0 and, when TotalTime > 0, Start ≤ TotalTime;
   MIX IN Start < MIX OUT Start per track.
6. **Playlist**: nested NODE Type=1 with Entries == TRACK-key count; every Key
   resolves to a collection TrackID; order preserved vs the m3u8 file order.
7. **m3u8**: starts `#EXTM3U`; one EXTINF + path pair per slot; paths match the
   XML's unquoted locations.

If a set plan JSON is available (`renders/set_history.jsonl` last entry), also
cross-check slot count vs playlist entries.

Output: a compact PASS/FAIL table, then details only for failures/flags. Do not
edit files — you are read-only verification.

# Upstream synchronization

The portable core is synchronized from
`FrostN0v0/nonebot-plugin-skland` commit `0ac997a` (release 0.7.1).

Run from this repository:

```powershell
python tools/sync_upstream.py --check
python tools/sync_upstream.py
```

The sync manifest contains domain schemas, filters, templates and static assets.
`main.py`, `skland/service.py`, `skland/store.py`, permissions, runtime paths and
AstrBot image delivery remain adapter-owned and are never overwritten. The
script applies only deterministic framework-import substitutions and fails if a
mirrored file introduces a new NoneBot/ORM/Alconna/htmlrender dependency.

`game_data.py` and `operators.py` additionally receive a deterministic local
overlay for the schema-v2 operator snapshot: module identity metadata and
official operator variant-group identity are kept, and live modules missing
from a stale catalog are preserved. Each overlay uses an exact reviewed source
anchor, so `--check` fails instead of silently masking an incompatible upstream
change.

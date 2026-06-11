"""Export a SetPlan to other DJ software — the "manual mode" deliverable (ADR 0010).

`mixer.render_set` is automatic mode (the machine plays the transitions);
this package is manual mode: the same plan handed to a human behind real
decks, with the ordering/key/energy work already done. Pure file writers —
no DB, no audio, no network.
"""

from dj.export.rekordbox import write_m3u8, write_rekordbox_xml
from dj.export.setsheet import write_setsheet

__all__ = ["write_m3u8", "write_rekordbox_xml", "write_setsheet"]

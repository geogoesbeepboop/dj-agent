"""Plan persistence + recently-played history (JSON on disk, tmp dir — no DB)."""

from dj.arc import Arc
from dj import persist
from dj.plan import SetPlan, Slot


def _plan(paths):
    arc = Arc.from_shape("s")
    n = max(len(paths) - 1, 1)
    return SetPlan(arc, [Slot(i / n, p, 120, "8A", -12) for i, p in enumerate(paths)])


def test_save_load_and_recent_paths(tmp_path):
    d = str(tmp_path)
    persist.save_plan(_plan(["a", "b"]), brief="one", approved=True, timestamp=1.0, out_dir=d)
    persist.save_plan(_plan(["c", "d"]), brief="two", approved=False, timestamp=2.0, out_dir=d)

    hist = persist.load_history(out_dir=d)
    assert [r["brief"] for r in hist] == ["one", "two"]
    assert hist[0]["plan"]["slots"][0]["path"] == "a"      # the plan round-trips

    assert persist.recent_paths(within=1, out_dir=d) == {"c", "d"}
    assert persist.recent_paths(within=2, out_dir=d) == {"a", "b", "c", "d"}
    assert persist.recent_paths(approved_only=True, out_dir=d) == {"a", "b"}


def test_recent_paths_empty_when_no_history(tmp_path):
    assert persist.recent_paths(out_dir=str(tmp_path)) == set()

import time

from pr_agent.servers.utils import DefaultDictWithTimeout


def test_default_dict_with_timeout_sweeps_expired_entries():
    # ttl=0 + refresh_interval=0 means every access should sweep anything already in the past.
    # update_key_time_on_get=False so accessing a different key doesn't refresh the stale one.
    d = DefaultDictWithTimeout(int, ttl=0, refresh_interval=0, update_key_time_on_get=False)
    d["stale"] = 5
    time.sleep(0.01)

    _ = d["trigger"]  # __getitem__ runs the refresh/sweep

    # Regresses the inverted throttle that left expired entries in memory forever.
    assert "stale" not in d

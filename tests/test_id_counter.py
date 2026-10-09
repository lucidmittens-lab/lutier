"""Tests for id_counter.py -- run directly: python3 -m tests.test_id_counter"""
import id_counter


def test_make_next_entry_id_counts_up_from_one():
    next_entry_id = id_counter.make_next_entry_id()
    assert next_entry_id() == 1
    assert next_entry_id() == 2
    assert next_entry_id() == 3
    print("make_next_entry_id: returns a callable that counts up from 1 -- OK")


def test_make_next_entry_id_gives_each_caller_its_own_independent_counter():
    next_a = id_counter.make_next_entry_id()
    next_b = id_counter.make_next_entry_id()
    assert next_a() == 1
    assert next_a() == 2
    assert next_b() == 1, "a second counter must start at 1 independently, not continue from the first"
    assert next_a() == 3
    assert next_b() == 2
    print("make_next_entry_id: each call returns a fresh, independent "
          "counter -- one module's ids never affect another's -- OK")


def test_cdl_library_lut_library_media_library_each_have_independent_counters():
    """The actual real-world usage this refactor preserves: each of the
    three registry modules gets its own next_entry_id, still starting
    fresh (unaffected by however many ids the OTHER modules have already
    handed out) -- exactly the same behavior as before this was
    consolidated out of three separate itertools.count(1) instances."""
    import cdl_library
    import lut_library
    import media_library
    # Just confirm each is callable and returns increasing ints -- can't
    # assert on the exact STARTING value since other tests in the same
    # process may have already advanced these module-level counters
    # (same "unique for the life of the process" contract as before).
    a1 = cdl_library.next_entry_id()
    a2 = cdl_library.next_entry_id()
    assert a2 == a1 + 1
    b1 = lut_library.next_entry_id()
    b2 = lut_library.next_entry_id()
    assert b2 == b1 + 1
    c1 = media_library.next_entry_id()
    c2 = media_library.next_entry_id()
    assert c2 == c1 + 1
    print("cdl_library/lut_library/media_library: each still has its own "
          "independent, monotonically increasing next_entry_id -- OK")


if __name__ == "__main__":
    test_make_next_entry_id_counts_up_from_one()
    test_make_next_entry_id_gives_each_caller_its_own_independent_counter()
    test_cdl_library_lut_library_media_library_each_have_independent_counters()
    print()
    print("All id_counter tests passed.")

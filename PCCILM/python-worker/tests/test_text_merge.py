"""features/text_merge.py: one unified copy per page, whatever order the captures arrive in."""

from __future__ import annotations

import time

from features.text_merge import MIN_OVERLAP, TextDocument, tokenize

LOREM = (
    "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et dolore "
    "magna aliqua Ut enim ad minim veniam quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo "
    "consequat Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
    "pariatur Excepteur sint occaecat cupidatat non proident sunt in culpa qui officia deserunt mollit anim id est "
    "laborum Curabitur pretium tincidunt lacus Nulla gravida orci a odio Nullam varius turpis et commodo pharetra "
    "est eros bibendum elit nec luctus magna felis sollicitudin mauris Integer in mauris eu nibh euismod gravida"
).split()


def words(a: int, b: int) -> str:
    return " ".join(LOREM[a:b])


def merged(doc: TextDocument) -> str:
    return " ".join(doc.text().split())


def test_first_capture_is_all_new() -> None:
    doc = TextDocument()
    r = doc.merge(words(0, 40))
    assert (r.new_words, r.new_ratio) == (40, 1.0)
    assert doc.word_count == 40


def test_scrolling_down_appends_without_duplicating() -> None:
    doc = TextDocument()
    doc.merge(words(0, 40))
    r = doc.merge(words(25, 70))  # 15 words overlap
    doc.merge(words(60, 100))
    assert merged(doc) == words(0, 100)
    assert len(doc.fragments) == 1
    assert r.new_words == 30


def test_repeat_capture_adds_nothing() -> None:
    doc = TextDocument()
    doc.merge(words(0, 40))
    r = doc.merge(words(10, 35))  # entirely inside what we have
    assert r.new_words == 0 and r.new_ratio == 0.0
    assert merged(doc) == words(0, 40)


def test_scrolling_up_prepends() -> None:
    doc = TextDocument()
    doc.merge(words(40, 80))
    doc.merge(words(20, 55))
    assert merged(doc) == words(20, 80)
    assert len(doc.fragments) == 1


def test_a_capture_that_contains_the_page_so_far_replaces_it() -> None:
    doc = TextDocument()
    doc.merge(words(20, 50))
    r = doc.merge(words(0, 90))
    assert merged(doc) == words(0, 90)
    assert r.new_words == 90 - 30


def test_capture_starting_mid_sentence_still_joins() -> None:
    doc = TextDocument()
    doc.merge(words(0, 40))
    text = "ipsum dolor " + words(25, 60)  # the first words are a cut-off start; overlap is still found
    doc.merge(text)
    assert len(doc.fragments) == 1
    assert merged(doc).endswith(words(55, 60))


def test_jump_far_down_makes_a_new_fragment_then_the_gap_joins_them() -> None:
    doc = TextDocument()
    doc.merge(words(0, 30))
    doc.merge(words(70, 100))
    assert len(doc.fragments) == 2
    doc.merge(words(20, 80))  # bridges both
    assert len(doc.fragments) == 1
    assert merged(doc) == words(0, 100)


def test_nothing_is_deleted_when_it_scrolls_away() -> None:
    doc = TextDocument()
    for a in range(0, 90, 20):
        doc.merge(words(a, a + 30))
    assert merged(doc) == words(0, 110)


def test_small_change_is_merged_and_the_newer_wording_wins() -> None:
    doc = TextDocument()
    first = words(0, 60).replace("Duis", "Counter 41")  # a live counter
    doc.merge(first)
    r = doc.merge(words(20, 80).replace("Duis", "Counter 42"))
    assert len(doc.fragments) == 1
    assert "Counter 42" in merged(doc) and "Counter 41" not in merged(doc)
    assert r.new_words > 0


def test_a_large_change_makes_a_new_fragment_instead_of_overwriting() -> None:
    doc = TextDocument()
    a = LOREM[0:40]
    b = list(a)
    for i in range(0, 40, 3):  # about a third differs
        b[i] = f"changed{i}"
    doc.merge(" ".join(a))
    doc.merge(" ".join(b))
    assert len(doc.fragments) == 2
    assert "Lorem" in doc.text()


def test_overlap_shorter_than_the_minimum_does_not_join() -> None:
    doc = TextDocument()
    doc.merge(words(0, 30))
    doc.merge(words(30 - (MIN_OVERLAP - 1), 60))  # one word short of a safe overlap
    assert len(doc.fragments) == 2


def test_short_repeated_phrase_does_not_join_unrelated_text() -> None:
    doc = TextDocument()
    doc.merge("Click here to read more about alpha beta gamma delta epsilon zeta eta theta")
    doc.merge("Click here to read more about one two three four five six seven eight nine")
    assert len(doc.fragments) == 2


def test_line_breaks_are_kept() -> None:
    doc = TextDocument()
    doc.merge("Title of the page here\nfirst paragraph begins and goes on for a while\nsecond paragraph")
    assert doc.text().splitlines()[0] == "Title of the page here"
    assert tokenize("a b\nc") == (["a", "b", "c"], [False, False, True])


def test_empty_text_changes_nothing() -> None:
    doc = TextDocument()
    r = doc.merge("   \n ")
    assert r.words == 0 and doc.word_count == 0


def test_round_trip_through_dict() -> None:
    doc = TextDocument()
    doc.merge(words(0, 40))
    doc.merge(words(70, 100))
    again = TextDocument.from_dict(doc.to_dict())
    assert again.text() == doc.text() and again.captures == doc.captures


def test_fifty_thousand_chars_merge_fast() -> None:
    page = " ".join(f"word{i % 977}x{i}" for i in range(4000))  # ~50,000 characters, no repeats
    assert 45_000 < len(page) < 55_000
    doc = TextDocument()
    doc.merge(page[: len(page) // 2 + 2000])
    start = time.perf_counter()
    doc.merge(page[len(page) // 2 - 2000:])
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert len(doc.fragments) == 1
    assert elapsed_ms < 100, f"merge took {elapsed_ms:.1f} ms"

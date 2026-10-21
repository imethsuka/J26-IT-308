"""adaptation/episodes.py: grouping buffered captures into episodes and one merged document per page."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from adaptation.episodes import ACTIVE_GAP_CAP, build_episodes, doc_id_for, split_episodes
from storage.raw_buffer import RawCapture, make_context_key
from tests.test_text_merge import words

T0 = datetime(2026, 10, 20, 9, 0, 0, tzinfo=timezone.utc)
_next_id = [0]


def cap(minutes: float, text: str | None, *, title: str = "Article", domain: str = "medium.com", idle: bool = False,
        score: float | None = 0.5, scrolls: int = 0) -> RawCapture:
    _next_id[0] += 1
    return RawCapture(
        id=_next_id[0], ts=T0 + timedelta(minutes=minutes), app="chrome", domain=domain, title=title,
        context_key=make_context_key("chrome", domain, title), context_record_id=None, decision="save", score=score,
        is_idle=idle, key_presses=1, mouse_clicks=0, mouse_scrolls=scrolls, mouse_moves=0, source="uia",
        chars=len(text or ""), truncated=False, text=text, images=(),
    )


def test_a_context_change_splits_the_episode() -> None:
    runs = split_episodes([cap(0, "a"), cap(1, "b"), cap(2, "c", title="Other"), cap(3, "d")])
    assert [len(r) for r in runs] == [2, 1, 1]


def test_a_gap_over_30_minutes_splits_but_30_does_not() -> None:
    runs = split_episodes([cap(0, "a"), cap(30, "b"), cap(61, "c")])
    assert [len(r) for r in runs] == [2, 1]


def test_returning_later_is_a_new_episode_of_the_same_document() -> None:
    day1 = [cap(0, words(0, 40)), cap(2, words(25, 70))]
    day2 = [cap(60 * 24 + 5, words(60, 100))]
    episodes, docs = build_episodes(day1 + day2)
    assert len(episodes) == 2
    assert episodes[0].doc_id == episodes[1].doc_id == doc_id_for(day1[0].context_key)
    assert (episodes[0].revisit, episodes[1].revisit) == (False, True)
    assert " ".join(docs[episodes[0].doc_id].text().split()) == words(0, 100)  # one copy across both episodes


def test_back_and_forth_between_two_pages_keeps_two_documents() -> None:
    a1, b1, a2 = cap(0, words(0, 40)), cap(1, words(50, 90), title="Other"), cap(2, words(25, 70))
    episodes, docs = build_episodes([a1, b1, a2])
    assert [e.title for e in episodes] == ["Article", "Other", "Article"]
    assert len(docs) == 2
    assert [e.revisit for e in episodes] == [False, False, True]


def test_words_added_counts_only_new_text() -> None:
    episodes, _ = build_episodes([cap(0, words(0, 40)), cap(1, words(0, 40)), cap(2, words(25, 70))])
    (e,) = episodes
    assert e.words_seen == 40 + 40 + 45
    assert e.words_added == 70


def test_active_time_skips_idle_and_caps_long_quiet_gaps() -> None:
    episodes, _ = build_episodes([
        cap(0, "x", idle=False), cap(2, "y", idle=True),   # 2 min active
        cap(10, "z", idle=False),                          # gap after an idle capture: not counted
        cap(25, "w"),                                      # 15 min quiet gap: capped at 5
    ])
    assert episodes[0].active_seconds == 120 + ACTIVE_GAP_CAP.total_seconds()


def test_totals_and_max_score() -> None:
    episodes, _ = build_episodes([cap(0, "a", score=0.2, scrolls=3), cap(1, "b", score=0.9, scrolls=4)])
    (e,) = episodes
    assert (e.n_captures, e.mouse_scrolls, e.key_presses, e.max_score) == (2, 7, 2, 0.9)


def test_captures_whose_text_was_dropped_still_count() -> None:
    episodes, docs = build_episodes([cap(0, None), cap(1, words(0, 40))])
    (e,) = episodes
    assert e.n_captures == 2 and e.words_added == 40


def test_input_order_does_not_matter() -> None:
    caps = [cap(0, words(0, 40)), cap(1, words(25, 70)), cap(2, words(60, 100))]
    forward, _ = build_episodes(caps)
    backward, _ = build_episodes(list(reversed(caps)))
    assert forward[0].capture_ids == backward[0].capture_ids and forward[0].words_added == backward[0].words_added

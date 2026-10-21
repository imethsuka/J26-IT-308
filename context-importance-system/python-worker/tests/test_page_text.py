from adaptation.page_text import clean_page, site_line_counts

ARTICLE = (
    "Solar capacity grew faster than any other source last year, according to a new industry report.\n"
    "Installations rose by about a third, led by large projects in Asia and Europe.\n"
)


def test_icon_glyphs_and_separators_are_dropped():
    text = "￼\n•\n.\n" + ARTICLE + "-\n"

    assert clean_page(text) == ARTICLE.strip()


def test_a_run_of_menu_labels_is_dropped_but_the_content_stays():
    menu = "File\nEdit\nSelection\nView\nGo\nRun\nTerminal\nHelp\n"

    assert clean_page(menu + ARTICLE) == ARTICLE.strip()


def test_a_heading_or_a_short_line_between_sentences_is_kept():
    text = ARTICLE + "Key findings\n" + ARTICLE + "Next steps\n"

    assert "Key findings" in clean_page(text) and "Next steps" in clean_page(text)


def test_four_short_lines_in_a_row_are_not_enough_to_call_it_a_menu():
    four = "Home\nProducts\nPricing\nContact\n"

    assert clean_page(four + ARTICLE).startswith("Home\nProducts\nPricing\nContact")


def test_a_repeated_short_label_is_kept_once():
    assert clean_page(ARTICLE + "Reply\n" + ARTICLE + "Reply\n").count("Reply") == 1


def test_a_long_line_repeated_is_not_touched():
    assert clean_page(ARTICLE + ARTICLE).count("Solar capacity") == 2


def test_lines_a_whole_site_repeats_are_furniture():
    footer = "Terms of service and privacy policy apply to every visit to this website."
    pages = [f"{footer}\nPage {n} has its own unique story about topic number {n} today.\n" for n in range(5)]
    site = site_line_counts([p.splitlines() for p in pages])

    cleaned = clean_page(pages[0], site=site)

    assert footer not in cleaned and "unique story" in cleaned


def test_a_line_on_too_few_pages_of_the_site_is_kept():
    shared = "This sentence appears on two pages only, so it is real content."
    pages = [[shared, "a"], [shared, "b"], ["c", "d"], ["e", "f"], ["g", "h"]]
    site = site_line_counts(pages)

    assert shared in clean_page(shared, site=site)


def test_a_site_with_fewer_than_three_pages_never_removes_anything_as_furniture():
    footer = "Terms of service and privacy policy apply to every visit to this website."
    site = site_line_counts([[footer], [footer]])

    assert footer in clean_page(footer, site=site)


def test_empty_and_whitespace_only_input():
    assert clean_page("") == "" and clean_page("   \n \n") == ""


def test_readable_falls_back_to_the_original_when_nothing_would_be_left():
    from adaptation.page_text import readable

    listing = "\n".join(f"file{n}.txt" for n in range(30))          # a page of short items: cleaning would empty it

    assert clean_page(listing) == ""
    assert readable(listing) == listing


def test_readable_uses_the_cleaned_page_when_enough_remains():
    from adaptation.page_text import readable

    menu = "File\nEdit\nSelection\nView\nGo\nRun\nTerminal\nHelp\n"

    assert readable(menu + ARTICLE * 3) == clean_page(menu + ARTICLE * 3)
    assert "Selection" not in readable(menu + ARTICLE * 3)

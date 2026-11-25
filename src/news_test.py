"""Unit tests for news matching and hard-news tagging - no API. Run: python src/news_test.py
"""

from urllib.parse import unquote_plus

from news import filter_relevant, is_hard_news, player_news_url


def test_hard_news_headlines_are_tagged():
    assert is_hard_news("Isak and Gakpo OUT of Liverpool vs Man City as Iraola gives injury update")
    assert is_hard_news("Saka ruled out for three weeks")
    assert is_hard_news("Midfielder suspended after fifth booking")


def test_opinion_pieces_are_not():
    assert not is_hard_news("Brighton players, Saka, Bruno: Buy, keep or sell in Gameweek 6?")
    assert not is_hard_news("Bank holiday banter as fans celebrate")  # 'ban' inside other words


def test_player_url_uses_surname_and_club():
    q = unquote_plus(player_news_url("E.Le Fée", "Sunderland"))
    assert '"Le Fée" Sunderland' in q and "injury" in q


def test_whole_word_name_match():
    items = [{"title": "United Kingdom news", "summary": ""}, {"title": "King scores again", "summary": ""}]
    assert [i["title"] for i in filter_relevant(items, ["King"])["King"]] == ["King scores again"]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()

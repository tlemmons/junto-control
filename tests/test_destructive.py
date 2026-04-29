from __future__ import annotations

from claudecontrol.destructive import matches_destructive


def test_no_match_clean_text() -> None:
    assert matches_destructive("hello world, just chatting") == []


def test_match_delete() -> None:
    hits = matches_destructive("please DELETE the row")
    assert hits and hits[0].lower() == "delete"


def test_match_drop_truncate() -> None:
    assert matches_destructive("DROP TABLE users") != []
    assert matches_destructive("TRUNCATE logs") != []


def test_match_lowercase() -> None:
    assert matches_destructive("delete this") != []


def test_match_deploy_keyword() -> None:
    assert matches_destructive("ready to deploy?") != []
    assert matches_destructive("push to production") != []


def test_match_git_push_force() -> None:
    assert matches_destructive("git push --force origin master") != []
    assert matches_destructive("git push -f origin master") != []


def test_no_match_partial_word() -> None:
    """`prod` should match standalone but not as substring."""
    assert matches_destructive("the prod server") != []
    assert matches_destructive("product manager said") == []


def test_no_match_predict() -> None:
    """`predict` contains 'redi' but no boundary-matched destructive token."""
    assert matches_destructive("predict the future") == []


def test_empty_string() -> None:
    assert matches_destructive("") == []

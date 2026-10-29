from skelly.conversation import CHAT_RULES, question_in, turn_reminder


def test_question_in_finds_the_last_question():
    assert question_in("Green! Top-tier spooky colour. Orange or purple?") == "Orange or purple?"
    assert question_in("Mine is candy corn yellow. What's yours: orange or purple?") == "orange or purple?"
    assert question_in("Have a spooky night, and come back soon!") is None


def test_reminder_lists_questions_already_asked():
    text = turn_reminder("Keep it short.", ["Orange or purple?", "Chocolate or gummies?"])
    assert text.startswith("Reminder: Keep it short.")
    assert '"Orange or purple?"' in text and '"Chocolate or gummies?"' in text
    assert "Don't ask any of those again" in text
    assert turn_reminder("Keep it short.", []) == "Reminder: Keep it short."


def test_chat_rules_forbid_repeating_questions():
    assert "never ask the same question twice" in CHAT_RULES

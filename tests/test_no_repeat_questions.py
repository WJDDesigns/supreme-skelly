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


def test_go_easy_on_puns():
    from skelly.callouts import COSTUME, DOG, GREETINGS, NOTICE, PLAIN
    from skelly.vision import PERSONAS

    assert "Go easy on puns" in CHAT_RULES
    assert "likes bone puns" not in PERSONAS["skelly"]
    assert not [line for line in PLAIN + COSTUME + NOTICE + DOG + GREETINGS if "pun" in line or "bone to pick" in line]


def test_catchphrases_are_spotted_and_retired():
    from skelly.conversation import catchphrases_in

    assert catchphrases_in("That's the spookiest thing I've seen in 300 years!") == [
        "anything about 300 years or centuries", '"spookiest" or "most terrifying"']
    assert catchphrases_in("Best costume in three hundred years.") == ["anything about 300 years or centuries"]
    assert catchphrases_in("Love the blue hat!") == []
    text = turn_reminder("Keep it short.", [], ["anything about 300 years or centuries"])
    assert "already said anything about 300 years" in text and "Don't say that again" in text
    assert "300 years" in CHAT_RULES and "never use the same compliment twice" in CHAT_RULES


def test_people_who_live_here_are_family():
    from skelly.conversation import LIVES_HERE, season_note

    for heard in ("I live here, Skelly!", "We live here.", "This is my house", "it's our house"):
        assert LIVES_HERE.search(heard), heard
    for heard in ("I live down the street", "Where do you live?", "Nice house"):
        assert not LIVES_HERE.search(heard), heard
    assert "never call them neighbour" in CHAT_RULES
    assert "live here is family" in season_note(__import__("datetime").datetime(2026, 10, 8, 19))

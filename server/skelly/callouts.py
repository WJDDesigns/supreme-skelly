"""Lines Skelly shouts to people walking past, so they come over to chat. Picked at random."""

from __future__ import annotations

import random

PLAIN = [
    "Psst! Hey you! Yes, you! Come here, I've got a bone to pick with you!",
    "Hellooo out there! Don't be shy, come say hi to a lonely skeleton!",
    "Excuse me! You there! Got a minute for a three-hundred-year-old?",
    "Hey! Over here! Nobody ever stops to chat with the skeleton!",
    "Yoo-hoo! Come closer, I promise I only rattle a little!",
    "Wait, wait, don't walk away! I've been standing here all night!",
    "Hey neighbour! Come over, I've got jokes so bad they're scary!",
    "Psst! Come here, I want to tell you a secret… it's spine-tingling!",
    "Oi! Fancy a chat with the most handsome skeleton on the street?",
    "Hello down there! Come visit, I won't bite… I don't have the lips for it!",
    "Hey! You look like someone who appreciates a good bone pun. Come here!",
    "Ahoy, passer-by! Stop and chat, I'm dying to talk to someone!",
]

COSTUME = [
    "Hey! You in the {c}! Come over here, I love your costume!",
    "Is that a {c}? Get over here, I need a closer look!",
    "Ooh, a {c}! Come say hi, monsters stick together!",
    "Hey, {c}! Yes you! Come chat with a fellow creature of the night!",
    "Wait, a {c} just walked by my yard? Come back, come back!",
    "Psst, {c}! Come here, I've got a spooky question for you!",
]


def call_out(costumes: list[str] | None = None) -> str:
    """A random shout to get someone's attention, about their costume when there is one."""
    if costumes and random.random() < 0.75:
        return random.choice(COSTUME).format(c=random.choice(costumes).lower())
    return random.choice(PLAIN)

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

# Something friendly Skelly noticed, as a noun phrase: "blue shirt", "cute dog", "running shoes".
NOTICE = [
    "Hey! Love the {n}! Come over and say hi to a lonely skeleton!",
    "Ooh, nice {n}! Got a minute to chat with a skeleton?",
    "Excuse me! Yes, you with the {n}! Come here, I've got to tell you something!",
    "Psst! Hey, great {n}! Come say hello, I don't bite!",
    "Hey neighbour! That {n} caught my eye socket! Come over here!",
    "Wow, look at that {n}! Come closer, I want a better look!",
]
DOG = [
    "Is that a dog? Bring them over! My dog Bonez would love to meet them!",
    "Hey! What a good pup! Come say hi, I love dogs!",
    "Ooh, a doggo! Come over here, I've got a bone… well, I am one!",
    "Psst! Your dog looks like my kind of friend! Come say hello!",
]


def call_out(costumes: list[str] | None = None, noticed: str = "") -> str:
    """A random shout to get someone's attention: about their costume, or something friendly Skelly noticed."""
    if costumes and random.random() < 0.75:
        return random.choice(COSTUME).format(c=random.choice(costumes).lower())
    if noticed and random.random() < 0.8:
        n = noticed.lower()
        if any(w in n.split() for w in ("dog", "dogs", "puppy", "pup", "doggo")) and random.random() < 0.6:
            return random.choice(DOG)
        return random.choice(NOTICE).format(n=n)
    return random.choice(PLAIN)

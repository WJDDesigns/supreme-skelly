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


# How Skelly opens a chat with someone who walked up, so it isn't the same "Hi, I'm Skelly" every time.
GREETINGS = [
    "Well hello there! Come closer, I don't bite… much.",
    "Oh! A visitor! Sorry, I was just resting my bones.",
    "Whoa! Is that a real live human? In MY yard? Welcome!",
    "Hey there, neighbour! Bonez and I were hoping someone would stop by.",
    "Ah, perfect timing! I've been practising my jokes all day and need an audience.",
    "Well, well, well, look who came to visit the most handsome skeleton on the street!",
    "Oh hello! Don't mind me, just standing here looking spooky and fabulous.",
    "Boo! Ha, gotcha. Just kidding, I'm the friendliest skeleton you'll ever meet.",
    "Hello, hello! You look like someone who appreciates a good bone pun.",
    "A visitor! Quick, Bonez, look alive! Well… you know what I mean.",
    "Welcome, welcome! Pull up a pumpkin, let's chat.",
    "Oh, hi there! I'd shake your hand, but mine might fall off.",
    "Ahoy! Three hundred years in this yard and you're my favourite visitor so far.",
    "Hey you! Yes, you! Come say hi, I've been dying for some company.",
]
_recent: list[str] = []


def greeting() -> str:
    """A random opening line, never one of the last few used."""
    line = random.choice([g for g in GREETINGS if g not in _recent] or GREETINGS)
    _recent.append(line)
    del _recent[:-5]
    return line

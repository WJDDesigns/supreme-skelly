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


# How he opens a chat when someone walks up or presses Start, so it isn't "Hi, I'm Skelly" every time.
GREETINGS = [
    "Well hello there! Come closer, I don't bite… much.",
    "Oh! A visitor! I was just about to doze off. Well, I would if I had eyelids.",
    "Welcome, welcome! Pull up a gravestone and stay a while!",
    "Hey there! You're the best thing I've seen all day, and I've seen a lot of squirrels.",
    "Boo! Ha, just kidding. Hi! I'm Skelly. Who are you?",
    "Ahh, fresh company! Do you know how boring it is standing in a yard all day?",
    "Well look who it is! Come on over, I've been saving my best jokes for you.",
    "Greetings, mortal! What brings you to my spooky little corner?",
    "Hey, hey! Don't mind the bones, I'm friendlier than I look!",
    "Oh good, someone to talk to! My dog Bonez is a terrible listener.",
    "Hi there! Fair warning, I'm a little bit rattled today. Get it? Rattled?",
    "Hello, friend! Lovely day for a chat with a skeleton, isn't it?",
]

WELCOME_BACK = [
    "{n}! You came back! I knew you couldn't stay away!",
    "Well, well, if it isn't {n}! My favourite visitor!",
    "{n}! Hey! I was hoping I'd see you again!",
    "Look who's back! Hi {n}! Did you miss me? I missed you!",
    "{n}, my friend! Welcome back to the spookiest yard on the street!",
    "Hey {n}! Good to see your face again. I don't have one, so I appreciate yours!",
]

_bags: dict[int, list[str]] = {}


def _draw(lines: list[str]) -> str:
    """Random, but every line comes up once before any repeats."""
    bag = _bags.get(id(lines))
    if not bag:
        bag = _bags[id(lines)] = random.sample(lines, len(lines))
    return bag.pop()


def greeting(name: str | None = None) -> str:
    """A fresh opening line for a chat: by name for someone he knows."""
    return _draw(WELCOME_BACK).format(n=name) if name else _draw(GREETINGS)

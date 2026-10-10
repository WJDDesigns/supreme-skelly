"""Lines the animatronic shouts to people walking past, and how it opens a chat. Picked at random.

Skelly (every skeleton model), Lethal Lily and Santa each have their own lines; see LINES.
"""

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


def call_out(costumes: list[str] | None = None, noticed: str = "", character: str = "skelly") -> str:
    """A random shout to get someone's attention: about their costume, or something friendly it noticed."""
    lines = LINES.get(character, LINES["skelly"])
    if costumes and random.random() < 0.75:
        return random.choice(lines["costume"]).format(c=random.choice(costumes).lower())
    if noticed and random.random() < 0.8:
        n = noticed.lower()
        if any(w in n.split() for w in ("dog", "dogs", "puppy", "pup", "doggo")) and random.random() < 0.6:
            return random.choice(lines["dog"])
        return random.choice(lines["notice"]).format(n=n)
    return random.choice(lines["plain"])


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


WELCOME_BACK = [
    "{n}! You came back! I knew you couldn't stay away!",
    "Well, well, if it isn't {n}! My favourite visitor!",
    "{n}! Hey! I was hoping I'd see you again!",
    "Look who's back! Hi {n}! Did you miss me? I missed you!",
    "{n}, my friend! Welcome back to the spookiest yard on the street!",
    "Hey {n}! Good to see your face again. I don't have one, so I appreciate yours!",
]


# Lethal Lily: a seven-foot Halloween witch with a glowing lantern.
LILY = {
    "plain": [
        "Psst! You there! Come closer, the lantern doesn't bite… and neither do I. Usually.",
        "Yoo-hoo! Come visit an old witch, I've been brewing up some jokes!",
        "Hello, dearie! Don't be shy, come warm yourself by my lantern!",
        "Wait, don't hurry off! I've been waiting all night for someone to chat with!",
        "Hey there, neighbour! Come here, I promise not to turn you into a toad!",
        "Psst! Come here, I've got a spell that only works on people who stop and say hi!",
        "Oh, a passer-by! Come closer, my crystal ball said you'd stop by!",
        "Over here, dearie! A witch gets lonely on a night like this!",
    ],
    "costume": [
        "Ooh, a {c}! Come here, dearie, let me get a look at you!",
        "Is that a {c}? Get over here, a witch knows a good costume when she sees one!",
        "Hey, {c}! Come say hello to a fellow creature of the night!",
        "A {c} walking past my lantern? Come back, come back!",
    ],
    "notice": [
        "Ooh, love the {n}, dearie! Come over and say hi!",
        "You with the {n}! Come here, I've got something to tell you!",
        "That {n} caught my eye! Come closer, let an old witch see!",
        "Psst! Lovely {n}! Come say hello, I don't bite!",
    ],
    "dog": [
        "Is that a dog? Bring them over, dearie! Every witch needs a familiar!",
        "What a good pup! Come say hi, I adore dogs!",
    ],
    "greetings": [
        "Well hello, dearie! Come closer, the lantern's nice and warm.",
        "Oh! A visitor! I was just stirring my cauldron.",
        "Welcome, welcome! Mind the broomstick, it's a little jumpy tonight.",
        "Hello there! My crystal ball said someone lovely would stop by.",
        "Ah, a guest! Come in close, let me get a good look at you.",
        "Well, well, well, who's come to visit the witch tonight?",
        "Hello, hello! Don't worry, I only turn people into toads on Tuesdays.",
        "Oh hello, dearie! Care to hear a spooky story?",
    ],
    "welcome_back": [
        "{n}! You came back to see me, dearie!",
        "Well, if it isn't {n}! My crystal ball said you'd return!",
        "{n}! Welcome back to my lantern!",
        "Look who's back! Hello again, {n}!",
    ],
}

# Ultra Santa: a jolly six-and-a-half-foot Santa Claus.
SANTA = {
    "plain": [
        "Ho ho ho! Hello there! Come say hi to Santa!",
        "Well hello! Come closer, I want to know if you've been good this year!",
        "Ho ho! Don't walk by without saying Merry Christmas!",
        "Hey there, neighbour! Come chat with Santa, the reindeer are busy!",
        "Psst! Come here, I've got a question for my list!",
        "Ho ho ho! Have you been naughty or nice? Come tell me!",
        "Hello, friend! Santa's been waiting all evening for someone to talk to!",
        "Wait, wait! Come back and say hello to Santa!",
    ],
    "costume": [
        "Ho ho ho! Is that a {c}? Come over here, I love it!",
        "Well look at that, a {c}! Come say hi to Santa!",
        "Hey, {c}! Come tell Santa what you want this year!",
        "A {c}! Come closer, I need a better look for my list!",
    ],
    "notice": [
        "Ho ho! Love the {n}! Come say hi to Santa!",
        "You with the {n}! Come here, Santa wants to say hello!",
        "Ooh, nice {n}! Is that on your Christmas list? Come tell me!",
        "Hey there! Great {n}! Come over and chat!",
    ],
    "dog": [
        "Ho ho! What a good dog! Bring them over, Santa has a treat list for pups too!",
        "Is that a puppy? Come say hi, the reindeer love dogs!",
    ],
    "greetings": [
        "Ho ho ho! Merry Christmas! Come closer!",
        "Well hello there! Have you been good this year?",
        "Ho ho! A visitor! The elves told me you'd stop by.",
        "Hello, hello! Come tell Santa what's on your list!",
        "Ho ho ho! Welcome! Mind the reindeer, Rudolph is a bit shy.",
        "Oh, hello! I was just checking my list twice.",
        "Merry Christmas, friend! It's so nice to see you!",
        "Ho ho! Come on over, Santa loves a visitor!",
    ],
    "welcome_back": [
        "Ho ho ho! {n}! You came back to see Santa!",
        "Well, if it isn't {n}! You're on my nice list, you know!",
        "{n}! Merry Christmas, my friend, welcome back!",
        "Look who's back! Hello again, {n}!",
    ],
}

LINES = {
    "skelly": {"plain": PLAIN, "costume": COSTUME, "notice": NOTICE, "dog": DOG,
               "greetings": GREETINGS, "welcome_back": WELCOME_BACK},
    "lily": LILY,
    "santa": SANTA,
}


def greeting(name: str | None = None, character: str = "skelly") -> str:
    """A random opening line, never one of the last few used; by name for someone it knows."""
    lines = LINES.get(character, LINES["skelly"])
    if name:
        return random.choice(lines["welcome_back"]).format(n=name)
    line = random.choice([g for g in lines["greetings"] if g not in _recent] or lines["greetings"])
    _recent.append(line)
    del _recent[:-5]
    return line

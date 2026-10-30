# Contributing to Supreme Skelly

Thanks for wanting to help! Supreme Skelly is open source under the [MIT license](LICENSE),
so you're welcome to use it, fork it and change it however you like. If you'd like your
change to come back into this project, here's how.

## Ideas and bugs

Open an issue. Say which animatronic you have (Ultra Skelly v2, 12 ft Skelly, Lethal Lily…),
what you did, what you expected and what happened. A screenshot helps a lot (blur out
anything personal first).

## Pull requests

1. Fork the repo and make a branch off `main`.
2. Set up and run the tests:

   ```bash
   python -m venv .venv && . .venv/bin/activate
   pip install -e ".[dev]"
   pytest
   ruff check .
   ```

   No Skelly nearby? Start the app in simulate mode (`SKELLY_SIMULATE=1`) to try the web page
   without Bluetooth.
3. Keep the change focused, and match the style of the code around it: plain Python in
   `server/skelly`, and a no-build web page in `web/`.
4. If it changes something people see, update the README (and add a screenshot from simulate
   mode in `docs/images/`).
5. Open the pull request and describe what changed and how you tested it.

Maintainers bump `VERSION` when releasing; you don't need to.

## Keep it private

This repo is public. Never commit real people's names or faces, camera names, IP addresses,
Wi-Fi names, API keys or recordings, not even in tests, screenshots or commit messages. Use
made-up names (Morticia, Gomez), generic cameras (Driveway, Front Door) and example
addresses (`192.168.1.x`). Everything personal lives in the `data/` folder on your own box,
which git ignores.

Found a security problem? Please report it privately (see [SECURITY.md](SECURITY.md)).

## License

By contributing, you agree that your contribution is licensed under the same
[MIT license](LICENSE) as the rest of the project.

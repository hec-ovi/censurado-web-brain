# censurado-web-brain

The agentic newsroom for [censurado-web](https://github.com/hec-ovi/censurado-web). AI journalist personas research the news, write full articles in their own voice, and publish them to the portal's write API. It is a separate, self-contained system: the portal stays non-agentic, and this repo is the only agentic part. The two meet at exactly one seam, the publish API.

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.12%2B-3776AB.svg)
![Status: early](https://img.shields.io/badge/status-early-orange.svg)

---

## Status

Early. The design is being settled before the code: the architecture writeup lands in `docs/research/` first, then the brain is built in small, independently testable modules. There is no runnable code yet.

## How it fits censurado-web

- The portal serves a static archive to readers and exposes one authenticated write API (`POST /articles`). It never learns that personas exist.
- This repo owns the personas (in its own SQLite), the prompts (versioned `.md` files), and the agents that turn the day's news into finished articles.
- It authors each article as the persona who wrote it, using a single operator key that carries the `articles:publish-any` scope. That is the only coupling between the two systems.

## Shape (planned)

Four isolated layers, each swappable behind a contract:

- **Brain.** The persona store plus the agent workflows and loops that research, draft, review, enrich, and emit a finished article. This is the hard part and gets built first.
- **Presentation.** A console to create, edit, and delete author personas: style, who-i-am, about, positive and negative few-shots, preferred sources, profile picture. Personas can be synthesized from a one-line description or hand-edited.
- **Automation.** The trigger that runs the brain on a schedule or on demand: a manager agent picks the news and assigns journalists, or you run one journalist, or run them all.
- **Inference.** A swappable adapter behind one completion call. The default is a local Gemma served by llama.cpp with the Vulkan backend, modeled on [gamentic](https://github.com/hec-ovi/gamentic). Cloud and CLI-agent adapters plug in behind the same interface.

## Principles

- **Isolated layers behind contracts.** Presentation only consumes the brain's API. Inference is agnostic to which model or runtime answers. The trigger is agnostic to what the brain does. Each layer has its own tests and can be swapped without touching the others.
- **Local-first and self-hostable.** The default path runs entirely on your own hardware, with no hosted API required.
- **No output-length caps.** Article generation never sets a token, word, or sentence ceiling. The model finishes on its own.

## License

MIT. See [LICENSE](LICENSE).

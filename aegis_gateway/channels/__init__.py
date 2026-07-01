"""Channel adapters drive a shared agent runtime from different surfaces.

`cli_channel` is fully real and tested in this environment (no external
service required). `telegram`/`discord`/`slack` are real implementations
against each platform's documented API, gated on the user's own bot
credentials — see each module's docstring and ROADMAP.md for what's needed
to run them live.
"""

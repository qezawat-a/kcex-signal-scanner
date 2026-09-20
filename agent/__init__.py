"""Agent package for the KCEX signal scanner.

Layout mirrors CRAG's agent core (agent-only parts, no XT/trading):
  providers.py  — multi-LLM provider detection + OpenAI-compatible chat (like CRAG brain.js/config.js)
  loop.py       — tool-calling agent loop with max rounds (like CRAG loop.js)
  tools.py      — scanner tools exposed to the LLM (scan/status/config/memory)
  memory.py     — persistent JSON memory, injected into the system prompt
  skills.py     — markdown playbook loader (skills/ folder)
  prompt.py     — system prompt assembly (SOUL + skills + memory)
  main.py       — CLI entry: --chat / --ask / --models / --list-skills
"""

"""Runtime AI gateway layer (RFC 0002 §5) — embedding providers behind a
minimal contract. Local-first: the default provider is Ollama on localhost so
vault content never leaves the machine; hosted providers are an explicit opt-in.
"""

"""Legacy shim — the real implementation now lives in src.lib.llm.

Kept so older imports (`from src.lib.claude import call_claude, ...`) keep working
while the codebase finishes cutting over to NVIDIA NIM.
"""

from src.lib.llm import (
    call_llm,
    call_llm_conversation,
    call_llm_vision,
)

call_claude = call_llm
call_claude_conversation = call_llm_conversation
call_claude_vision = call_llm_vision

__all__ = [
    "call_claude",
    "call_claude_conversation",
    "call_claude_vision",
    "call_llm",
    "call_llm_conversation",
    "call_llm_vision",
]

"""Chat transcripts.

PoC storage, same tradeoff as uploads: a process-local dict, so restarting the
server forgets every conversation. See the storage TODO in README.md.
"""

import threading
from dataclasses import dataclass, field
from uuid import uuid4

_lock = threading.Lock()
_conversations: dict[str, "Conversation"] = {}


@dataclass
class Turn:
    role: str  # "user" or "assistant"
    text: str  # what the page renders
    # Assistant turns keep the raw content blocks from the API. Claude needs
    # its own thinking blocks replayed unchanged to continue the same
    # reasoning, so we resend these rather than the flattened text.
    blocks: list | None = None


@dataclass
class Conversation:
    id: str
    # The stored name of the document this conversation is about. Every
    # conversation has one. Set once, when the conversation is created.
    document: str
    turns: list[Turn] = field(default_factory=list)

    @property
    def needs_attachment(self) -> bool:
        """True when the document still has to be sent to Claude.

        It rides in the first user message; the stateless API resends the
        whole history after that, so it goes on the wire exactly once here.
        """
        return not self.turns

    def as_messages(self) -> list[dict]:
        """The transcript in Messages API shape."""
        return [
            {"role": turn.role, "content": turn.blocks if turn.blocks else turn.text}
            for turn in self.turns
        ]

    def add_user(self, text: str, blocks: list | None = None) -> None:
        """Add a question. `blocks` carries the attachment on the first turn."""
        self.turns.append(Turn(role="user", text=text, blocks=blocks))

    def add_assistant(self, text: str, blocks: list) -> None:
        self.turns.append(Turn(role="assistant", text=text, blocks=blocks))

    def drop_last_user_turn(self) -> None:
        """Undo an unanswered question so a retry doesn't duplicate it."""
        if self.turns and self.turns[-1].role == "user":
            self.turns.pop()


def get_or_create(conversation_id: str | None, document: str) -> Conversation:
    """The conversation for this id, creating one if the id is unknown.

    `document` only applies to a conversation being created -- an existing one
    keeps whatever it was opened about.
    """
    with _lock:
        if conversation_id and conversation_id in _conversations:
            return _conversations[conversation_id]
        conversation = Conversation(id=uuid4().hex, document=document)
        _conversations[conversation.id] = conversation
        return conversation


def drop(conversation_id: str | None) -> None:
    with _lock:
        _conversations.pop(conversation_id, None)


def reset_all() -> None:
    """Forget every conversation. Used by tests."""
    with _lock:
        _conversations.clear()

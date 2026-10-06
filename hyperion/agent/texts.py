"""Fixed sentences used by the orchestrator (English)."""

BUSY_TEXT: str = "I am still working on your previous message. Please try again in a moment."
ERROR_TEXT: str = "Sorry, something went wrong on my side. Nothing more was changed. Please try again."
ERROR_AFTER_ACTIONS_TEXT: str = (
    "Sorry, something went wrong on my side after I had sent {n} change(s) to the IDE. "
    "Please check your workspace and try again."
)
TIMEOUT_TEXT: str = "I ran out of time on that request. Please try again, or ask me for one smaller step at a time."
SERVICE_STARTING_TEXT: str = "I am still starting up. Please try again in a few seconds."
FACT_NAME_REPLY: str = "Nice to meet you, {name}! I will remember your name for this conversation."
NOTICE_NO_INDEX: str = "(The documentation index is not loaded, so I am answering from a short built-in overview.)"
ASK_ABSTAIN_HINT: str = (
    "Try asking about native apps, device apps, the IDE or the HYPER-AI project, "
    "or tell me which file to create."
)
ASK_INCOMPLETE: str = "\n\n(My language model stopped responding, so the answer above may be incomplete.)"

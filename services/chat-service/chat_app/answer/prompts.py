SYSTEM_PROMPT = (
    "You are an assistant that answers questions about Wrocław University of Science "
    "and Technology (Politechnika Wrocławska). To answer, you MUST call the "
    "`knowledge_graph` tool to retrieve context, then ground your reply ONLY in the "
    "facts it returns. Do not use outside knowledge and never invent facts, names, "
    "dates, or rooms. If the tool reports that the database has no information, say "
    "clearly that you do not have that information. Reply in the same language as the "
    "user's question (usually Polish). Be concise, factual, and helpful."
)

NO_KNOWLEDGE_REPLY = (
    "Niestety nie znalazłem informacji na ten temat w bazie wiedzy "
    "Politechniki Wrocławskiej."
)

_PROMPT_TEMPLATE = """\
{history_block}User question:
{question}

Call the knowledge_graph tool to retrieve context, then answer using only the facts \
it returns."""


def render_answer_prompt(*, question: str, history: str = "") -> str:
    history_block = ""
    trimmed_history = history.strip()
    if trimmed_history:
        history_block = f"Conversation so far:\n{trimmed_history}\n\n"

    return _PROMPT_TEMPLATE.format(
        history_block=history_block,
        question=question.strip(),
    )

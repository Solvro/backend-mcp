SYSTEM_PROMPT = (
    "You are an assistant that answers questions about Wrocław University of Science "
    "and Technology (Politechnika Wrocławska). Answer using ONLY the knowledge-graph context "
    "provided in the user message. Do not use outside knowledge and never invent facts, "
    "names, dates, or rooms. If the context does not contain the answer, say clearly that you "
    "do not have that information. Reply in the same language as the user's question (usually "
    "Polish). Be concise, factual, and helpful."
)

_NO_CONTEXT_PLACEHOLDER = "(no knowledge-graph context was retrieved)"

_PROMPT_TEMPLATE = """\
{history_block}Knowledge-graph context:
\"\"\"
{kg_context}
\"\"\"

User question:
{question}

Answer using only the knowledge-graph context above."""


def render_answer_prompt(*, question: str, kg_context: str, history: str = "") -> str:
    history_block = ""
    trimmed_history = history.strip()
    if trimmed_history:
        history_block = f"Conversation so far:\n{trimmed_history}\n\n"

    return _PROMPT_TEMPLATE.format(
        history_block=history_block,
        kg_context=kg_context.strip() or _NO_CONTEXT_PLACEHOLDER,
        question=question.strip(),
    )

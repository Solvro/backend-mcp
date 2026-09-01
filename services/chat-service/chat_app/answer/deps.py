from dataclasses import dataclass

from chat_app.mcp_gateway import KnowledgeGraphGateway


@dataclass
class AnswerDeps:
    gateway: KnowledgeGraphGateway
    trace_id: str | None = None
    tool_called: bool = False
    knowledge_retrieved: bool = False

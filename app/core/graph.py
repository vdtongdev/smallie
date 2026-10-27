"""Per-user knowledge graph in plain Postgres tables (graph_nodes, graph_edges in app/core/db.py).

Nodes are the user ("User"), their uploaded files and named entities. Edges are facts that touch
the user or a file: learned from chats (`extract_facts`, after the orchestrator's replies) or from
a document card (`save_card`, one model call per upload). Document content stays in the PGVector
chunks; reaching a file node scopes the passage search to that file (`documents_for`).

Every query is filtered by user_id. Functions are sync; callers wrap them in asyncio.to_thread.
"""

import asyncio
import re
import unicodedata
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Connection, and_, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from app.core.config import get_settings
from app.core.db import documents, get_engine, graph_edges, graph_nodes
from app.core.llm import get_chat_model, get_embeddings
from app.core.prompts import EXTRACT_PROMPT

# Cosine similarity at which a new entity name is taken as one already in the graph. Measured on
# the default embedding model: suffix variants ("Acme Corp"/"ACME Corporation", "Microsoft"/
# "Microsoft Corporation", "United States"/"USA") 0.89-0.91; different names up to 0.82 ("long term"/
# "short term"; two banks or two people 0.73). Aliases ("Saigon", "Apple Inc."/"Apple" 0.80) stay
# separate nodes: a duplicate is safer than merging two different things.
# shortcut: tuned for one model, re-measure after changing OPENROUTER_EMBED_MODEL.
SAME_ENTITY = 0.85
# Relations with one value per user: a new value replaces the old edge. EXTRACT_PROMPT lists them.
FUNCTIONAL = frozenset({"has_name", "lives_in", "works_at", "has_role", "risk_tolerance", "investment_horizon",
                        "prefers_language"})
# The relations EXTRACT_PROMPT asks for. Between two nodes, one of these wins over an invented one.
STANDARD = FUNCTIONAL | {"holds", "interested_in", "has_skill", "studied_at", "has_goal", "prefers"}
# Wordings free models used for a standard relation (seen in real extractions).
ALIASES = {
    "is_named": "has_name", "named": "has_name", "is_called": "has_name", "name_is": "has_name",
    "has_full_name": "has_name", "works_for": "works_at", "employed_at": "works_at", "employed_by": "works_at",
    "lives_at": "lives_in", "resides_in": "lives_in", "studies_at": "studied_at", "graduated_from": "studied_at",
    "educated_at": "studied_at", "is_interested_in": "interested_in", "interests": "interested_in",
    "has_skills": "has_skill", "skilled_in": "has_skill", "has_risk_tolerance": "risk_tolerance",
    "has_risk_profile": "risk_tolerance", "risk_profile": "risk_tolerance",
    "has_investment_horizon": "investment_horizon", "preferred_language": "prefers_language",
    "has_goal_of": "has_goal", "goal": "has_goal",
}
RECALL_FACTS = 20  # facts about the user put into every prompt, newest first
MAX_FACTS = 8  # per extraction call (scripts.build_graph sends several turns at once)
SEARCH_NODES = 5  # nodes nearest to a search_memory query
MIN_TEXT_MATCH = 4  # shorter entity names found in a question would scope searches by accident
EXTRACT_ANSWER_CHARS = 3000  # enough for a questionnaire's conclusion
USER = "User"
USER_WORDS = frozenset({"user", "the user", "i", "me", "myself"})

_EDGE_PUNCT = " \t\n.,;:!?\"'()[]{}<>“”‘’"


def normalize(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split()).strip(_EDGE_PUNCT)


class Fact(BaseModel):
    subject: str = Field(min_length=1, max_length=80)
    relation: str = Field(pattern=r"^[a-z][a-z_]{1,39}$")
    object: str = Field(min_length=1, max_length=120)

    @field_validator("relation", mode="before")
    @classmethod
    def snake_case(cls, value: str) -> str:
        relation = re.sub(r"[\s-]+", "_", str(value).strip().lower())
        return ALIASES.get(relation, relation)


class FactUpdate(BaseModel):
    """Durable facts about the user from these chat turns; empty when there are none."""

    facts: list[Fact] = Field(default_factory=list, max_length=MAX_FACTS)

    @field_validator("facts", mode="before")
    @classmethod
    def first_ones(cls, value):
        # A few facts too many shouldn't throw the whole reply away.
        return value[:MAX_FACTS] if isinstance(value, list) else value


# ---------- nodes ----------

def _node_id(conn: Connection, user_id: str, kind: str, norm: str) -> str | None:
    return conn.execute(select(graph_nodes.c.id).where(
        graph_nodes.c.user_id == user_id, graph_nodes.c.kind == kind, graph_nodes.c.norm == norm,
        graph_nodes.c.document_id.is_(None),
    )).scalar()


def _get_or_create(conn: Connection, user_id: str, kind: str, name: str, embedding=None) -> str:
    norm = normalize(name)
    conn.execute(insert(graph_nodes).values(
        id=str(uuid.uuid4()), user_id=user_id, kind=kind, name=name.strip(), norm=norm, embedding=embedding,
    ).on_conflict_do_nothing(index_elements=["user_id", "kind", "norm"], index_where=graph_nodes.c.document_id.is_(None)))
    return _node_id(conn, user_id, kind, norm)


def _user_node(conn: Connection, user_id: str) -> str:
    return _get_or_create(conn, user_id, "user", USER)


def _file_node(conn: Connection, user_id: str, document_id: str) -> str | None:
    """The file's node; None when the document is gone or belongs to someone else."""
    filename = conn.execute(select(documents.c.filename).where(
        documents.c.id == document_id, documents.c.user_id == user_id)).scalar()
    if filename is None:
        return None
    conn.execute(insert(graph_nodes).values(
        id=str(uuid.uuid4()), user_id=user_id, kind="file", name=filename, norm=normalize(filename),
        document_id=document_id,
    ).on_conflict_do_nothing(index_elements=["document_id"]))
    return conn.execute(select(graph_nodes.c.id).where(graph_nodes.c.document_id == document_id)).scalar()


def _entity(conn: Connection, user_id: str, name: str, embedding, create: bool = True) -> str | None:
    """Exact name match, else the nearest entity at >= SAME_ENTITY, else a new node (if `create`)."""
    if found := _node_id(conn, user_id, "entity", normalize(name)):
        return found
    if embedding is not None:
        distance = graph_nodes.c.embedding.cosine_distance(embedding)
        nearest = conn.execute(select(graph_nodes.c.id, distance.label("distance")).where(
            graph_nodes.c.user_id == user_id, graph_nodes.c.kind == "entity", graph_nodes.c.embedding.is_not(None),
        ).order_by(distance).limit(1)).first()
        if nearest and 1 - nearest.distance >= SAME_ENTITY:
            return nearest.id
    return _get_or_create(conn, user_id, "entity", name, embedding) if create else None


def _stem(filename: str) -> str:
    """The normalized file name without its extension."""
    return normalize(filename.rsplit(".", 1)[0] if "." in filename else filename)


def _user_files(conn: Connection, user_id: str) -> dict[str, list[str]]:
    """normalized filename -> every document id with that name, for the user's searchable files."""
    files: dict[str, list[str]] = {}
    for filename, document_id in conn.execute(select(documents.c.filename, documents.c.id).where(
            documents.c.user_id == user_id, documents.c.status != "failed")):
        files.setdefault(normalize(filename), []).append(document_id)
    return files


def _drop_orphans(conn: Connection, user_id: str) -> None:
    linked = exists().where(or_(graph_edges.c.source_id == graph_nodes.c.id, graph_edges.c.target_id == graph_nodes.c.id))
    conn.execute(delete(graph_nodes).where(graph_nodes.c.user_id == user_id, graph_nodes.c.kind == "entity", ~linked))


def _embed(names) -> dict[str, list[float]]:
    names = sorted(set(names))
    return dict(zip(names, get_embeddings().embed_documents(names))) if names else {}


# ---------- edges ----------

def _link(conn: Connection, user_id: str, source_id: str, relation: str, target_id: str, source: str,
          document_id: str | None = None, conversation_id: str | None = None) -> None:
    conn.execute(insert(graph_edges).values(
        id=str(uuid.uuid4()), user_id=user_id, source_id=source_id, relation=relation, target_id=target_id,
        source=source, document_id=document_id, conversation_id=conversation_id,
    ).on_conflict_do_update(
        index_elements=["user_id", "source_id", "relation", "target_id"], set_={"updated_at": func.now()},
    ))


def _keep_one_edge(conn: Connection, user_id: str, source_id: str, target_id: str, relation: str) -> bool:
    """One learned edge per pair of nodes, since two relations between the same pair were almost
    always one fact worded twice ("has_income_type"/"has_financial_characteristic"). Standard
    relations win over invented ones, the newest invented one over older ones, and two different
    standard relations both stay (holds and works_at the same company). False: skip the new edge."""
    pair = and_(graph_edges.c.user_id == user_id, graph_edges.c.source_id == source_id,
                graph_edges.c.target_id == target_id, graph_edges.c.relation != relation,
                graph_edges.c.source != "document")  # card edges are the file's, not learned facts
    existing = set(conn.execute(select(graph_edges.c.relation).where(pair)).scalars())
    if relation not in STANDARD and existing & STANDARD:
        return False
    conn.execute(delete(graph_edges).where(pair, graph_edges.c.relation.not_in(STANDARD)))
    return True


def save_facts(user_id: str, facts: list[Fact], source: str, conversation_id: str | None = None) -> int:
    """Store facts that touch the user or one of their files; returns how many were kept."""
    with get_engine().connect() as conn:
        files = _user_files(conn, user_id)
    is_entity = lambda name: normalize(name) not in USER_WORDS and normalize(name) not in files  # noqa: E731
    vectors = _embed(n.strip() for f in facts for n in (f.subject, f.object) if is_entity(n))
    kept = 0
    with get_engine().begin() as conn:
        user_node = _user_node(conn, user_id)
        anchors = {user_node}

        def resolve(name: str) -> str | None:
            norm = normalize(name)
            if norm in USER_WORDS:
                return user_node
            if norm in files:
                node = _file_node(conn, user_id, files[norm][0])
                anchors.add(node)
                return node
            return _entity(conn, user_id, name.strip(), vectors.get(name.strip()))

        for fact in facts:
            subject, target = resolve(fact.subject), resolve(fact.object)
            if not subject or not target or subject == target or not ({subject, target} & anchors):
                continue  # general knowledge, not about the user or their files
            if target == user_node and subject in anchors:
                subject, target = target, subject  # "<file> is_cv_of User" is the same link as User -> file
            if subject == user_node and fact.relation in FUNCTIONAL:
                conn.execute(delete(graph_edges).where(
                    graph_edges.c.user_id == user_id, graph_edges.c.source_id == user_node,
                    graph_edges.c.relation == fact.relation, graph_edges.c.target_id != target))
            if _keep_one_edge(conn, user_id, subject, target, fact.relation):
                _link(conn, user_id, subject, fact.relation, target, source, conversation_id=conversation_id)
                kept += 1
        _drop_orphans(conn, user_id)
    return kept


def card_text(filename: str, summary: str | None) -> str:
    return f"{filename}: {summary}" if summary else filename


def save_card(user_id: str, document_id: str, summary: str, entities: list[str]) -> None:
    """A document card: the file's summary plus File -mentions-> Entity edges. Re-running replaces it."""
    entities = [e.strip() for e in entities if e.strip()]
    with get_engine().connect() as conn:
        filename = conn.execute(select(documents.c.filename).where(
            documents.c.id == document_id, documents.c.user_id == user_id)).scalar()
    if filename is None:
        return  # deleted while the card was being written
    card = card_text(filename, summary)
    vectors = _embed([*entities, card])  # one request for the entities and the card
    with get_engine().begin() as conn:
        file_node = _file_node(conn, user_id, document_id)
        if file_node is None:
            return  # deleted while the card was being written
        conn.execute(update(documents).where(documents.c.id == document_id).values(summary=summary, card_embedding=vectors[card]))
        conn.execute(delete(graph_edges).where(graph_edges.c.user_id == user_id, graph_edges.c.document_id == document_id))
        for name in entities:
            entity = _entity(conn, user_id, name, vectors.get(name))
            _link(conn, user_id, file_node, "mentions", entity, "document", document_id=document_id)
        _drop_orphans(conn, user_id)


# ---------- reading ----------

_S, _T = aliased(graph_nodes), aliased(graph_nodes)


def _facts_query(user_id: str):
    return (select(graph_edges.c.id, graph_edges.c.relation, graph_edges.c.source, graph_edges.c.created_at,
                   _S.c.name.label("subject"), _T.c.name.label("object"))
            .join(_S, _S.c.id == graph_edges.c.source_id).join(_T, _T.c.id == graph_edges.c.target_id)
            .where(graph_edges.c.user_id == user_id))


def _line(row) -> str:
    return f"{row.subject} {row.relation.replace('_', ' ')} {row.object}"


def recall_facts(user_id: str, limit: int = RECALL_FACTS) -> str:
    """The newest facts about the user, as a bullet list for the prompt."""
    with get_engine().connect() as conn:
        rows = conn.execute(_facts_query(user_id).where(or_(_S.c.kind == "user", _T.c.kind == "user"))
                            .order_by(graph_edges.c.updated_at.desc()).limit(limit)).all()
    return "\n".join(f"- {_line(r)}" for r in rows)


def search_facts(user_id: str, query: str) -> str:
    """Facts around the entities nearest to `query` (search_memory)."""
    embedding = get_embeddings().embed_query(query)
    with get_engine().connect() as conn:
        nearest = select(graph_nodes.c.id).where(
            graph_nodes.c.user_id == user_id, graph_nodes.c.embedding.is_not(None),
        ).order_by(graph_nodes.c.embedding.cosine_distance(embedding)).limit(SEARCH_NODES).scalar_subquery()
        rows = conn.execute(_facts_query(user_id).where(
            or_(graph_edges.c.source_id.in_(nearest), graph_edges.c.target_id.in_(nearest)))
            .order_by(graph_edges.c.updated_at.desc()).limit(RECALL_FACTS)).all()
    return "\n".join(f"- {_line(r)}" for r in rows)


def list_facts(user_id: str) -> list[dict]:
    """What the assistant learned from the user (not the document cards), for /api/memories."""
    with get_engine().connect() as conn:
        rows = conn.execute(_facts_query(user_id).where(graph_edges.c.source != "document")
                            .order_by(graph_edges.c.updated_at.desc())).all()
    return [{"id": r.id, "text": _line(r), "source": r.source, "created_at": r.created_at} for r in rows]


def delete_fact(user_id: str, edge_id: str) -> bool:
    with get_engine().begin() as conn:
        deleted = conn.execute(delete(graph_edges).where(
            graph_edges.c.id == edge_id, graph_edges.c.user_id == user_id)).rowcount
        _drop_orphans(conn, user_id)
    return bool(deleted)


def drop_orphans(user_id: str) -> None:
    with get_engine().begin() as conn:
        _drop_orphans(conn, user_id)


def _linked_files(conn: Connection, user_id: str, node_ids) -> list[str]:
    """Document ids of the file nodes one edge away from `node_ids`."""
    other = aliased(graph_nodes)
    touches = or_(and_(graph_edges.c.source_id.in_(node_ids), graph_edges.c.target_id == other.c.id),
                  and_(graph_edges.c.target_id.in_(node_ids), graph_edges.c.source_id == other.c.id))
    return list(conn.execute(select(other.c.document_id).distinct().join(graph_edges, touches).where(
        graph_edges.c.user_id == user_id, other.c.kind == "file")).scalars())


def has_cards(user_id: str) -> bool:
    with get_engine().connect() as conn:
        return conn.execute(select(exists().where(
            documents.c.user_id == user_id, documents.c.card_embedding.is_not(None)))).scalar()


def card_files(user_id: str, vector: list[float], limit: int) -> list[str]:
    """The user's files whose card is nearest to `vector`."""
    with get_engine().connect() as conn:
        return list(conn.execute(select(documents.c.id).where(
            documents.c.user_id == user_id, documents.c.status != "failed", documents.c.card_embedding.is_not(None),
        ).order_by(documents.c.card_embedding.cosine_distance(vector)).limit(limit)).scalars())


def documents_for(user_id: str, query: str, entity: str = "") -> list[str]:
    """The files a passage search should be limited to: those named by `entity` or in `query`
    (extension optional), else linked to `entity`, else to known entity names that appear in
    `query`; [] means search every file."""
    with get_engine().connect() as conn:
        files = _user_files(conn, user_id)
        by_stem: dict[str, list[str]] = {}
        for name, ids in files.items():
            by_stem.setdefault(_stem(name), []).extend(ids)
        if (named := normalize(entity)) and (found := files.get(named) or by_stem.get(named)):
            return found
        padded = f" {normalize(query)} "
        if found := [i for stem, ids in by_stem.items() if len(stem) >= MIN_TEXT_MATCH
                     and re.search(rf"(?<!\w){re.escape(stem)}(?!\w)", padded) for i in ids]:
            return found
        if entity.strip():
            node = _entity(conn, user_id, entity.strip(), get_embeddings().embed_query(entity), create=False)
            if node and (ids := _linked_files(conn, user_id, [node])):
                return ids
        # Weak models often leave `entity` empty (AP-006): look for known names in the question too.
        names = conn.execute(select(graph_nodes.c.id, graph_nodes.c.norm).where(
            graph_nodes.c.user_id == user_id, graph_nodes.c.kind == "entity",
            func.length(graph_nodes.c.norm) >= MIN_TEXT_MATCH)).all()
        hits = [node_id for node_id, norm in names if re.search(rf"(?<!\w){re.escape(norm)}(?!\w)", padded)]
        return _linked_files(conn, user_id, hits) if hits else []


# ---------- extraction ----------

def format_turn(question: str, answer: str, answer_chars: int = EXTRACT_ANSWER_CHARS) -> str:
    return f"User: {question}\n\nAssistant: {answer[:answer_chars]}"


async def extract_facts(user_id: str, question: str, answer: str, callbacks: list, source: str = "chat",
                        conversation_id: str | None = None) -> int:
    """Learn what one finished turn says about the user. A plain model call after the reply,
    because free models rarely chose to call a save tool themselves (AP-006)."""
    return await extract_from_turns(user_id, format_turn(question, answer), callbacks, source, conversation_id)


async def extract_from_turns(user_id: str, turns: str, callbacks: list, source: str = "chat",
                             conversation_id: str | None = None) -> int:
    """One extraction call over `turns` (one or more formatted chat turns); returns facts kept."""
    def known() -> tuple[list[str], list[str]]:
        with get_engine().connect() as conn:
            files = conn.execute(select(documents.c.filename).where(
                documents.c.user_id == user_id, documents.c.status != "failed")).scalars()
            relations = conn.execute(select(graph_edges.c.relation).distinct().where(
                graph_edges.c.user_id == user_id, graph_edges.c.source != "document")).scalars()
            return list(files), sorted(set(relations) - STANDARD)

    files, relations = await asyncio.to_thread(known)
    # Names go into the message, not a template: file names may contain braces.
    turn = (f"The user's files: {', '.join(files)}\n" if files else "") + \
        (f"Relations already in the user's graph: {', '.join(relations)}\n" if relations else "") + \
        ("\n" if files or relations else "") + turns
    model = get_chat_model(get_settings().openrouter_orchestrator_model or None)
    extractor = model.with_structured_output(FactUpdate, method="function_calling")
    update_ = await extractor.ainvoke([SystemMessage(EXTRACT_PROMPT), HumanMessage(turn)], config={"callbacks": callbacks})
    if not update_.facts:
        return 0
    return await asyncio.to_thread(save_facts, user_id, update_.facts, source, conversation_id)

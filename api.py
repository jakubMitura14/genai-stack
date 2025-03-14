import os
from langchain_neo4j import Neo4jGraph
from dotenv import load_dotenv
from utils import (
    create_vector_index,
    BaseLogger,
)
from chains import (
    load_embedding_model,
    load_llm,
    configure_llm_only_chain,
    configure_qa_rag_chain,
    generate_ticket,
)
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from langchain.callbacks.base import BaseCallbackHandler
from threading import Thread
from queue import Queue, Empty
from collections.abc import Generator
from sse_starlette.sse import EventSourceResponse
from fastapi.middleware.cors import CORSMiddleware
import json

load_dotenv(".env")

url = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")
ollama_base_url = os.getenv("OLLAMA_BASE_URL")
embedding_model_name = os.getenv("EMBEDDING_MODEL")
llm_name = os.getenv("LLM")
# Remapping for Langchain Neo4j integration
os.environ["NEO4J_URL"] = url

embeddings, dimension = load_embedding_model(
    embedding_model_name,
    config={"ollama_base_url": ollama_base_url},
    logger=BaseLogger(),
)

# Initialize Neo4j graph and RAG chain as None first
neo4j_graph = None
rag_chain = None

# Try to connect to Neo4j, but don't fail if it's not available
try:
    # if Neo4j is local, you can go to http://localhost:7474/ to browse the database
    neo4j_graph = Neo4jGraph(
        url=url, username=username, password=password, refresh_schema=False
    )
    create_vector_index(neo4j_graph)
    logger = BaseLogger()
    logger.info("Successfully connected to Neo4j database")
except Exception as e:
    logger = BaseLogger()
    logger.warning(f"Could not connect to Neo4j database: {str(e)}")
    logger.warning("RAG functionality will be disabled")

# Load LLM regardless of Neo4j connection status
llm = load_llm(
    llm_name, logger=BaseLogger(), config={"ollama_base_url": ollama_base_url}
)

llm_chain = configure_llm_only_chain(llm)

# Only configure RAG if Neo4j connection was successful
if neo4j_graph is not None:
    try:
        rag_chain = configure_qa_rag_chain(
            llm, embeddings, embeddings_store_url=url, username=username, password=password
        )
        logger.info("RAG chain configured successfully")
    except Exception as e:
        logger.warning(f"Failed to configure RAG chain: {str(e)}")
        rag_chain = None


class QueueCallback(BaseCallbackHandler):
    """Callback handler for streaming LLM responses to a queue."""

    def __init__(self, q):
        self.q = q

    def on_llm_new_token(self, token: str, **kwargs) -> None:
        self.q.put(token)

    def on_llm_end(self, *args, **kwargs) -> None:
        return self.q.empty()


def stream(cb, q) -> Generator:
    job_done = object()

    def task():
        try:
            x = cb()
        except Exception as e:
            q.put(f"Error: {str(e)}")
        finally:
            q.put(job_done)

    t = Thread(target=task)
    t.start()

    content = ""

    # Get each new token from the queue and yield for our generator
    while True:
        try:
            next_token = q.get(True, timeout=1)
            if next_token is job_done:
                break
            content += next_token
            yield next_token, content
        except Empty:
            continue


app = FastAPI()
origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"message": "Hello World", "model": llm_name, "rag_available": rag_chain is not None}


class Question(BaseModel):
    text: str
    rag: bool = False


class BaseTicket(BaseModel):
    text: str


@app.get("/query-stream")
def qstream(question: Question = Depends()):
    output_function = llm_chain
    if question.rag:
        if rag_chain is None:
            raise HTTPException(status_code=400, detail="RAG functionality is not available due to Neo4j connection issues")
        output_function = rag_chain

    q = Queue()

    def cb():
        output_function.invoke(question.text, config={"callbacks": [QueueCallback(q)]})

    def generate():
        yield json.dumps({"init": True, "model": llm_name})
        for token, _ in stream(cb, q):
            yield json.dumps({"token": token})

    return EventSourceResponse(generate(), media_type="text/event-stream")


@app.get("/query")
async def ask(question: Question = Depends()):
    output_function = llm_chain
    if question.rag:
        if rag_chain is None:
            raise HTTPException(status_code=400, detail="RAG functionality is not available due to Neo4j connection issues")
        output_function = rag_chain

    try:
        result = output_function.invoke(question.text)
        return {"result": result, "model": llm_name}
    except Exception as e:
        logger.error(f"Error processing query: {str(e)}")
        return {"result": f"Error: {str(e)}", "model": llm_name}


@app.get("/generate-ticket")
async def generate_ticket_api(question: BaseTicket = Depends()):
    if neo4j_graph is None:
        raise HTTPException(status_code=400, detail="Ticket generation is not available due to Neo4j connection issues")

    try:
        new_title, new_question = generate_ticket(
            neo4j_graph=neo4j_graph,
            llm_chain=llm_chain,
            input_question=question.text,
        )
        return {"result": {"title": new_title, "text": new_question}, "model": llm_name}
    except Exception as e:
        logger.error(f"Error generating ticket: {str(e)}")
        return {"result": f"Error: {str(e)}", "model": llm_name}

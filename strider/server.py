"""Simple ReasonerStdAPI server."""
from typing import List, Dict

import aiosqlite
from fastapi import Depends, FastAPI
from starlette.staticfiles import StaticFiles

from strider.models import Query, Message
from strider.setup_query import execute_query

app = FastAPI(
    title='Strider/ARAGORN/Ranking Agent',
    description='Translator Autonomous Relay Agent',
    version='1.0.0',
)

app.mount("/static", StaticFiles(directory="ui"), name="static")


async def get_db():
    """Get SQLite connection."""
    async with aiosqlite.connect('results.db') as db:
        yield db


# @app.post('/query', response_model=Message, tags=['query'])
# async def answer_query(query: Query) -> Message:
@app.post('/query', response_model=str, tags=['query'])
async def answer_query(query: Query) -> str:
    """Answer biomedical question."""
    query_id = await execute_query(query.message.query_graph.dict())
    return query_id


@app.get('/results')
async def get_results(
        query_id: str,
        db=Depends(get_db),
):
    """Get results for a query."""
    # get column names
    statement = f'PRAGMA table_info("{query_id}")'
    cursor = await db.execute(
        statement,
    )
    results = await cursor.fetchall()
    columns = [row[1] for row in results]

    # get result rows
    statement = f'SELECT * FROM "{query_id}"'
    cursor = await db.execute(
        statement,
    )
    results = await cursor.fetchall()

    # zip 'em up
    return [
        dict(zip(columns, row))
        for row in results
    ]


predicates_type = Dict[str, Dict[str, List[str]]]


@app.get('/predicates', response_model=predicates_type, tags=['predicates'])
async def get_predicates() -> predicates_type:
    """Get available predicates."""
    return {}

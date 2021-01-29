"""Simple ReasonerStdAPI server."""
import uuid
import asyncio
import itertools
import json
import logging
import os
import enum
import pprint
from typing import Dict

from fastapi import Body, Depends, FastAPI, HTTPException, BackgroundTasks
from fastapi.openapi.docs import (
    get_swagger_ui_html,
)
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx
from starlette.middleware.cors import CORSMiddleware

from reasoner_pydantic import Query, Message, Response as ReasonerResponse

from .fetcher import StriderWorker
from .query_planner import generate_plans, NoAnswersError
from .scoring import score_graph
from .results import get_db, Database
from .storage import RedisGraph, RedisList
from .config import settings
from .logging import LogLevelEnum
from .util import standardize_graph_lists

LOGGER = logging.getLogger(__name__)

APP = FastAPI(
    title='Strider/ARAGORN/Ranking Agent',
    description='Translator Autonomous Relay Agent',
    version='1.0.0',
    terms_of_service="http://robokop.renci.org:7055/tos?service_long=Strider&provider_long=the%20Renaissance%20Computing%20Institute&provider_short=RENCI",
    docs_url=None,
    redoc_url=None,
    servers=[{"url": f"{settings.server_url}"}],
)
APP.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@APP.on_event("startup")
async def print_config():
    pretty_config = pprint.pformat(
        settings.dict()
    )
    LOGGER.info(f" App Configuration:\n {pretty_config}")

EXAMPLE = {
    "message": {
        "query_graph": {
            "nodes": {
                "n0": {
                    "id": "MONDO:0001056",
                    "category": "biolink:Disease"
                },
                "n1": {
                    "category": "biolink:Disease"
                }
            },
            "edges": {
                "e01": {
                    "subject": "n0",
                    "object": "n1",
                    "predicate": "biolink:related_to"
                }
            }
        }
    }
}


def get_finished_query(
        qid: str,
        log_level: str,
) -> dict:
    qgraph = RedisGraph(f"{qid}:qgraph")
    kgraph = RedisGraph(f"{qid}:kgraph")
    results = RedisList(f"{qid}:results")
    logs = RedisList(f"{qid}:log")

    expiration_seconds = int(settings.store_results_for.total_seconds())

    qgraph.expire(expiration_seconds)
    kgraph.expire(expiration_seconds)
    results.expire(expiration_seconds)
    logs.expire(expiration_seconds)

    # pylint: disable=protected-access
    levelno = logging._nameToLevel[log_level]
    # pylint: disable=protected-access
    filtered_logs = [
        log for log in logs.get() if logging._nameToLevel[log['level']] >= levelno
    ]

    return dict(
        message=dict(
            query_graph=qgraph.get(),
            knowledge_graph=kgraph.get(),
            results=list(results.get()),
        ),
        logs=filtered_logs,
    )


async def process_query(
        qid: str,
        log_level: str,
):
    # Set up workers
    strider = StriderWorker(num_workers=2)
    await strider.setup(qid)

    # Generate plan
    try:
        await strider.generate_plan()
    except NoAnswersError:
        # End early with no results
        # (but we should have log messages)
        return get_finished_query(qid, log_level)

    # Process
    await strider.run(qid, wait=True)

    # Pull results from redis
    # Also starts timer for expiring results
    return get_finished_query(qid, log_level)


@APP.post('/aquery', tags=['query'])
async def async_query(
        background_tasks: BackgroundTasks,
        query: Query = Body(..., example=EXAMPLE),
        log_level: LogLevelEnum = LogLevelEnum.ERROR,
) -> dict:
    """Start query processing."""
    # Generate Query ID
    qid = str(uuid.uuid4())[:8]

    query_graph = query.dict()['message']['query_graph']
    standardize_graph_lists(query_graph)

    # Save query graph to redis
    redis_query_graph = RedisGraph(f"{qid}:qgraph")
    redis_query_graph.set(query_graph)

    # Start processing
    background_tasks.add_task(process_query, qid, log_level)

    # Return ID
    return dict(id=qid)


@APP.post('/query_result', response_model=ReasonerResponse)
async def get_results(
        qid: str,
        log_level: LogLevelEnum = LogLevelEnum.ERROR,
) -> dict:
    """ Get results for a running or finished query """
    return get_finished_query(qid, log_level)


@APP.post('/query', tags=['query'])
async def sync_query(
        query: Query = Body(..., example=EXAMPLE),
        log_level: LogLevelEnum = LogLevelEnum.ERROR,
) -> dict:
    """Handle synchronous query."""
    # Generate Query ID
    qid = str(uuid.uuid4())[:8]

    query_graph = query.dict()['message']['query_graph']
    standardize_graph_lists(query_graph)

    # Save query graph to redis
    redis_query_graph = RedisGraph(f"{qid}:qgraph")
    redis_query_graph.set(query_graph)

    # Process query and wait for results
    query_results = await process_query(qid, log_level)

    # Return results
    return query_results


@APP.post('/ars')
async def handle_ars(
        data: Dict,
):
    """Handle ARS message."""
    if data.get('model', None) != 'tr_ars.message':
        raise HTTPException(
            status_code=400,
            detail='Not a valid Translator message',
        )
    data = data['fields']
    if data.get('ref', None) is not None:
        raise HTTPException(
            status_code=400,
            detail='Not head message',
        )
    if data.get('data', None) is not None:
        data = json.loads(data['data'])
    elif data.get('url', None) is not None:
        data = httpx.get(data['url'], timeout=60).json()
    else:
        raise HTTPException(
            status_code=400,
            detail='Not a valid tr_ars.message',
        )

    content = await sync_answer(data)
    headers = {'tr_ars.message.status': 'A'}
    return JSONResponse(content=content, headers=headers)

APP.mount("/static", StaticFiles(directory="static"), name="static")


@APP.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    """Customize Swagger UI."""
    return get_swagger_ui_html(
        openapi_url=APP.openapi_url,
        title=APP.title + " - Swagger UI",
        oauth2_redirect_url=APP.swagger_ui_oauth2_redirect_url,
        swagger_favicon_url="/static/favicon.png",
    )


async def _get_results(
        query_id: str,
        since: float = None,
        limit: int = None,
        offset: int = 0,
        database=None,
):
    """Get results."""
    # get column names from results db
    columns = await database.get_columns(query_id)

    kgraph = {
        'nodes': dict(),
        'edges': dict(),
    }
    results = []
    for row in await extract_results(query_id, since, limit, offset, database):
        result, _kgraph = parse_bindings(dict(zip(columns, row)))
        results.append(result)
        kgraph['nodes'].update(_kgraph['nodes'])
        kgraph['edges'].update(_kgraph['edges'])
    # convert kgraph nodes and edges to list format
    kgraph = {
        'nodes': list(kgraph['nodes'].values()),
        'edges': list(kgraph['edges'].values()),
    }
    return {
        'knowledge_graph': kgraph,
        'results': results
    }


def parse_bindings(bindings):
    """Parse bindings into message format."""
    kgraph = {
        'nodes': dict(),
        'edges': dict(),
    }
    result = {
        'node_bindings': [],
        'edge_bindings': [],
    }
    for key, element in bindings.items():
        if key.startswith('_'):
            result[key[1:]] = element
            continue
        kid = element.pop('kid')
        qid = element.pop('qid')
        element.pop('kid_qid', None)
        if key.startswith('e_'):
            result['edge_bindings'].append({
                'qg_id': qid,
                'kg_id': kid,
            })
            kgraph['edges'][kid] = {
                'id': kid,
                **element,
            }
        else:
            result['node_bindings'].append({
                'qg_id': qid,
                'kg_id': kid,
            })
            kgraph['nodes'][kid] = {
                'id': kid,
                **element,
            }
    return result, kgraph


async def extract_results(query_id, since, limit, offset, database):
    """Extract results from database."""
    statement = f'SELECT * FROM "{query_id}"'
    if since is not None:
        statement += f' WHERE _timestamp >= {since}'
    statement += ' ORDER BY _timestamp ASC'
    if limit is not None:
        statement += f' LIMIT {limit}'
    if offset:
        statement += f' OFFSET {offset}'
    rows = await database.execute(statement)
    return [
        tuple(
            json.loads(value) if isinstance(value, str) else value
            for value in row
        )
        for row in rows
    ]


@APP.post('/plan', response_model=Dict, tags=['query'])
async def generate_traversal_plan(
        query: Query,
) -> list[Dict]:
    """Generate plans for traversing knowledge providers."""
    query_graph = query.message.query_graph.dict()
    return await generate_plans(query_graph)


@APP.post('/score', response_model=Message, tags=['query'])
async def score_results(
        query: Query,
) -> Message:
    """Score results."""
    message = query.message.dict()
    identifiers = {
        knode['id']: knode.get('equivalent_identifiers', [])
        for knode in message['knowledge_graph']['nodes']
    }
    for result in message['results']:
        graph = {
            'nodes': {
                nb['qg_id']: {
                    'qid': nb['qg_id'],
                    'kid': nb['kg_id'],
                    'equivalent_identifiers': identifiers[nb['kg_id']]
                }
                for nb in result['node_bindings']
            },
            'edges': {
                eb['qg_id']: {
                    'qid': eb['qg_id'],
                    'kid': eb['kg_id'],
                }
                for eb in result['edge_bindings']
            }
        }
        result['score'] = await score_graph(
            graph,
            message['query_graph'],
        )
    return message

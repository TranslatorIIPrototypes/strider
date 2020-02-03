"""async prioritizer (worker)."""
import asyncio
import json
import logging
import random
import sqlite3

import aioredis
import aiormq

from strider.graph_walking import get_paths
from strider.neo4j import HttpInterface
from strider.scoring import score_graph
from strider.worker import Worker

LOG_FORMAT = ('%(levelname) -10s %(asctime)s %(name) -30s %(funcName) '
              '-35s %(lineno) -5d: %(message)s')
LOGGER = logging.getLogger(__name__)
HOSTNAME = 'localhost'


class Prioritizer(Worker):
    """Asynchronous worker to consume results and publish jobs."""

    IN_QUEUE = 'results'

    def __init__(self, *args, **kwargs):
        """Initialize."""
        super().__init__(*args, **kwargs)
        self.neo4j = None
        self.redis = None
        self.sql = None

    async def setup(self):
        """Set up Neo4j and Redis connections."""
        self.neo4j = HttpInterface(
            url=f'http://{HOSTNAME}:7474',
            # credentials={
            #     'username': 'neo4j',
            #     'password': 'ncatsgamma',
            # },
        )
        self.redis = await aioredis.create_redis_pool(
            f'redis://{HOSTNAME}'
        )
        self.sql = sqlite3.connect('results.db')

    async def is_done(self, plan, qid=None, kid=None):
        """Return True iff a job (qid/kid) has already been completed."""
        return bool(await self.redis.sismember(f'{plan}_done', f'({kid}:{qid})'))

    async def on_message(self, message):
        """Handle message from results queue.

        The message should be jsonified dictionary:
        type: object
        properties:
          execution_plan:
            type: string
          source:
            type: object
            properties:
              kid:
                type: string
              qid:
                type: string
          edge:
            type: object
            properties:
              kid:
                type: string
              qid:
                type: string
          target:
            type: object
            properties:
              kid:
                type: string
              qid:
                type: string
        """
        if self.neo4j is None:
            await self.setup()

        # parse message
        data = json.loads(message.body)
        result_id = f'({data["source"]["qid"]}:{data["source"]["kid"]})' \
                    f'-[{data["edge"]["qid"]}:{data["edge"]["kid"]}]-' \
                    f'({data["target"]["qid"]}:{data["target"]["kid"]})'
        LOGGER.debug("[result %s]: Processing...", result_id)

        # store result
        # merge with existing nodes/edge, but update edge weight
        statement = f'''MERGE (n0:{data['execution_plan']} {{kid:"{data['source']['kid']}", qid:"{data['source']['qid']}"}})
                        MERGE (n1:{data['execution_plan']} {{kid:"{data['target']['kid']}", qid:"{data['target']['qid']}"}})
                        MERGE (n0)-[e:{data['execution_plan']} {{kid:"{data['edge']['kid']}", qid:"{data['edge']['qid']}"}}]->(n1)
                        SET e.weight = {random.randint(1, 10)}
                        '''
        self.neo4j.run(statement)

        # compute priority:
        # get subgraphs
        subgraphs = [path.to_dict() for path in get_paths(
            query_id=data['execution_plan'], kid=data['target']['kid'], qid=data['target']['qid']
        )]

        slots = {
            x.decode('utf-8')
            for x in await self.redis.smembers(f'{data["execution_plan"]}_slots')
        }

        # compute scores
        scores = [score_graph(subgraph) for subgraph in subgraphs]
        # for each subgraph, add its weight to each component node's priority
        answers = []
        for subgraph, score in zip(subgraphs, scores):
            for node in subgraph['nodes'].values():
                # if await self.redis.hexists(f'{data["execution_plan"]}_done', node['label']):
                #     continue
                await self.redis.hincrbyfloat(f'{data["execution_plan"]}_priorities', f'({node["kid"]}:{node["qid"]})', score)
            subgraph_things = {qid for qid in subgraph['nodes'].keys()}
            subgraph_things |= {qid for qid in subgraph['edges'].keys()}
            if subgraph_things == slots:
                answers.append(subgraph)

        # publish answers to the results DB
        LOGGER.debug("[result %s]: Storing answers %s", result_id, str(answers))
        if answers:
            rows = []
            for answer in answers:
                things = {**answer['nodes'], **answer['edges']}
                values = [things[qid]['kid'] for qid in slots]
                rows.append(values)
            placeholders = ', '.join(['?' for _ in range(len(rows[0]))])
            columns = ', '.join([f'`{qid}`' for qid in slots])
            with self.sql:
                self.sql.executemany(
                    f'''INSERT OR IGNORE INTO `{data['execution_plan']}` ({columns}) VALUES ({placeholders})''',
                    rows
                )

        # publish all nodes to jobs queue
        nodes = list({
            (node['qid'], node['kid'])
            for subgraph in subgraphs
            for node in subgraph['nodes'].values()
            if not await self.is_done(data["execution_plan"], qid=node['qid'], kid=node['kid'])
        })
        for qid, kid in nodes:
            job = {
                'execution_plan': data["execution_plan"],
                'qid': qid,
                'kid': kid,
            }
            job_id = f'({kid}:{qid})'
            LOGGER.debug("[result %s]: Queueing job %s", result_id, job_id)
            priority = min(255, int(float(await self.redis.hget(f'{data["execution_plan"]}_priorities', job_id))))
            await self.channel.basic_publish(
                routing_key='jobs',
                body=json.dumps(job).encode('utf-8'),
                properties=aiormq.spec.Basic.Properties(
                    priority=priority,
                ),
            )
        return

        # black-list any old jobs for these nodes
        # *not necessary if priority only increases


if __name__ == "__main__":
    # add FileHandler to root logger
    logging.basicConfig(
        filename='logs/strider.log',
        format=LOG_FORMAT,
        level=logging.DEBUG,
    )

    prioritizer = Prioritizer()

    # start event loop
    loop = asyncio.get_event_loop()
    loop.run_until_complete(prioritizer.run())
    loop.run_forever()
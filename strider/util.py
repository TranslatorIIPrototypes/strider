"""General utilities."""
import re
from typing import Callable, Union

import httpx
import yaml
from bmt import Toolkit as BMToolkit


def camel_to_snake(s, sep=' '):
    return re.sub(r'(?<!^)(?=[A-Z])', sep, s).lower()


def snake_to_camel(s):
    return ''.join(word.title() for word in s.split(' '))


class WrappedBMT():
    """ 
    Wrapping around some of the BMT Toolkit functions
    to provide case conversions to the new format
    """

    def __init__(self):
        self.bmt = BMToolkit()
        self.all_slots = self.bmt.get_all_slots()
        self.all_slots_formatted = ['biolink:' + s.replace(' ', '_')
                                    for s in self.all_slots]
        self.prefix = 'biolink:'

    def new_case_to_old_case(self, s):
        """
        Convert new biolink case format (biolink:GeneOrGeneProduct)
        to old case format (gene or gene product)

        Also works with slots (biolink:related_to -> related to)
        """
        s = s.replace(self.prefix, '')
        if s in self.all_slots_formatted:
            return s.replace('_', ' ')
        else:
            return camel_to_snake(s)

    def old_case_to_new_case(self, s):
        """
        Convert old case format (gene or gene product)
        to new biolink case format (biolink:GeneOrGeneProduct)

        Also works with slots (related to -> biolink:related_to)
        """
        if s in self.all_slots:
            return self.prefix + s.replace(' ', '_')
        else:
            return self.prefix + snake_to_camel(s)

    def get_descendants(self, concept):
        """ Wrapped BMT descendants function that does case conversions """
        concept_old_format = self.new_case_to_old_case(concept)
        descendants_old_format = self.bmt.get_descendants(concept_old_format)
        descendants = [self.old_case_to_new_case(d)
                       for d in descendants_old_format]
        if len(descendants) == 0:
            descendants.append(concept)
        return descendants

    def get_ancestors(self, concept):
        """ Wrapped BMT ancestors function that does case conversions """
        concept_old_format = self.new_case_to_old_case(concept)
        ancestors_old_format = self.bmt.get_ancestors(concept_old_format)
        ancestors = [self.old_case_to_new_case(a)
                     for a in ancestors_old_format]
        if len(ancestors) == 0:
            ancestors.append(concept)
        return ancestors


async def post_json(url, request):
    """Make post request."""
    async with httpx.AsyncClient(verify=False, timeout=None) as client:
        response = await client.post(
            url,
            json=request,
        )
        response.raise_for_status()
        return response.json()


class KPError(Exception):
    """Exception in a KP request."""


def setup_logging():
    """Set up logging."""
    with open('logging_setup.yml', 'r') as stream:
        config = yaml.load(stream.read(), Loader=yaml.SafeLoader)
    logging.config.dictConfig(config)


def ensure_list(arg: Union[str, list[str]]) -> list[str]:
    """Enclose in list if necessary."""
    if isinstance(arg, list):
        return arg
    return [arg]


def listify_value(input_dictionary: dict[str, any], key: str):
    """ If the provided key is not a list, wrap it in a list """
    if key not in input_dictionary:
        return
    if isinstance(input_dictionary[key], str):
        input_dictionary[key] = [input_dictionary[key]]


def standardize_graph_lists(graph: dict[str, dict]):
    """ Convert fields that are given as a string to a list """
    node_fields = ['id', 'category']
    for node in graph['nodes'].values():
        for field in node_fields:
            listify_value(node, field)

    edge_fields = ['predicate']
    for edge in graph['edges'].values():
        for field in edge_fields:
            listify_value(edge, field)


def message_to_list_form(message):
    """Convert *graph nodes/edges and node/edge bindings to list forms."""
    if message['results']:
        message['results'] = [
            {
                'node_bindings': [
                    {
                        'qg_id': qg_id,
                        **binding,
                    }
                    for qg_id, binding in result['node_bindings'].items()
                ],
                'edge_bindings': [
                    {
                        'qg_id': qg_id,
                        **binding,
                    }
                    for qg_id, binding in result['edge_bindings'].items()
                ],
            } for result in message.get('results', [])
        ]
    if message['knowledge_graph']['nodes']:
        message['knowledge_graph']['nodes'] = [
            {
                'id': node['id'],
                **node,
            }
            for node in message['knowledge_graph']['nodes']
        ]
    if message['knowledge_graph']['edges']:
        message['knowledge_graph']['edges'] = [
            {
                'id': edge['id'],
                **edge,
            }
            for edge in message['knowledge_graph']['edges']
        ]
    return message


def message_to_dict_form(message):
    """Convert *graph nodes/edges and node/edge bindings to dict forms."""
    if message['results']:
        if isinstance(message['results'][0]['node_bindings'], list):
            message['results'] = [
                {
                    'node_bindings': {
                        binding['qg_id']: [binding]
                        for binding in result['node_bindings']
                    },
                    'edge_bindings': {
                        binding['qg_id']: [binding]
                        for binding in result['edge_bindings']
                    },
                } for result in message.get('results', [])
            ]
        elif not isinstance(
                list(message['results'][0]['node_bindings'].values())[0],
                dict
        ):
            message['results'] = [
                {
                    'node_bindings': {
                        key: [{'kg_id': el} for el in ensure_list(bindings)]
                        for key, bindings in result['node_bindings'].items()
                    },
                    'edge_bindings': {
                        key: [{'kg_id': el} for el in ensure_list(bindings)]
                        for key, bindings in result['edge_bindings'].items()
                    },
                } for result in message.get('results', [])
            ]
    if message['knowledge_graph']['nodes']:
        message['knowledge_graph']['nodes'] = {
            node['id']: node
            for node in message['knowledge_graph']['nodes']
        }
    if message['knowledge_graph']['edges']:
        message['knowledge_graph']['edges'] = {
            edge['id']: edge
            for edge in message['knowledge_graph']['edges']
        }
    return message


def graph_to_dict_form(graph):
    """Convert query_graph or knowledge_graph to dict form."""
    return {
        'nodes': {
            node['id']: node
            for node in graph['nodes']
        },
        'edges': {
            edge['id']: edge
            for edge in graph['edges']
        }
    }


def deduplicate_by(elements: list, fcn: Callable):
    """De-duplicate list via a function of each element."""
    return list(dict((fcn(element), element) for element in elements).values())


def remove_null_values(obj):
    """Remove null values from all dicts in JSON-able object."""
    if isinstance(obj, dict):
        return {
            key: remove_null_values(value)
            for key, value in obj.items()
            if value is not None
        }
    elif isinstance(obj, list):
        return [
            remove_null_values(el)
            for el in obj
        ]
    else:
        return obj

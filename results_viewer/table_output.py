"""Script to poll database and output a table in the terminal."""
import json
import threading

import httpx
import pandas as pd

pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)
pd.set_option('display.max_colwidth', None)


def main():
    """Submit question and begin polling."""
    with open('example.json', 'r') as f:
        request = json.load(f)
    response = httpx.post("http://robokop.renci.org:5781/query", json=request)
    assert response.status_code < 300
    query_id = response.json()
    poll(query_id)


def poll(query_id):
    """Poll the backend every second and display the table."""
    try:
        response = httpx.get(f"http://robokop.renci.org:5781/results?query_id={query_id}")
        assert response.status_code < 300
        table = response.json()
        # for index, row in enumerate(table):
        #     response = httpx.get(
        #         "https://nodenormalization-sri.renci.org/get",
        #         params={'key': list(row.values())}
        #     )
        #     print(index)
        #     assert response.status_code < 300
        #     details = response.json()
        #     for key, value in row.items():
        #         value = details[value]
        #         if value is None:
        #             continue
        #         table[index][key] = value['id']['label']
        print(pd.DataFrame(table))
    except ConnectionError:
        print("Could not connect. Polling again...")
    threading.Timer(1, lambda: poll(query_id)).start()


if __name__ == "__main__":
    main()

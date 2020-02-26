document.addEventListener('DOMContentLoaded', () => {
  function drawTable(list) {
    const table = document.getElementById('table');
    table.innerHTML = '';
    const columnCount = Object.keys(list[0]).length;
    let row = table.insertRow(-1);
    for (let i = 0; i < columnCount; i += 1) {
      const headerCell = document.createElement('TH');
      headerCell.innerHTML = Object.keys(list[0])[i];
      row.appendChild(headerCell);
    }
    for (let i = 0; i < list.length; i += 1) {
      row = table.insertRow(-1);
      for (let j = 0; j < columnCount; j += 1) {
          const cell = row.insertCell(-1);
          const colName = Object.keys(list[0])[j];
          cell.innerHTML = list[i][colName];
      }
  }
  }
  function poll(qid) {
    fetch(`http://robokop.renci.org:5781/results?query_id=${qid}`, {
      method: 'GET',
    })
      .then((response) => response.json())
      .then((res) => {
        console.log(res);
        if (res.length) {
          drawTable(res);
        }
      })
      .catch((err) => {
        console.log(err);
      });
      setTimeout(() => poll(qid), 1000);
  }
  document.getElementById('submit').addEventListener('click', async () => {
    let input = document.getElementById('input').value;
    fetch('http://robokop.renci.org:5781/query', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        // 'Cache-Control': 'no-cache',
      },
      // mode: 'no-cors',
      body: input,
    })
      .then((response) => response.json())
      .then((res) => {
        poll(res);
      })
      .catch((err) => {
        console.log(err);
      });
  });
});
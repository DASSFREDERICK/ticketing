/* Helix assistant front-end: loaded after the main script, replaces the old chat handlers. */
(function () {
  const chips = document.querySelector('.chips');
  const DEF = [['How many tickets?', 'How many tickets do I have?'], ['In progress', 'Which tickets are in progress?'],
               ['Summary', 'Summarize my tickets'], ['Create a ticket', 'Create a ticket']];
  const setChips = list => {
    chips.innerHTML = '';
    list.forEach(c => {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = c[0];
      if (c[2]) b.dataset.t = c[2]; else b.dataset.q = c[1];
      chips.appendChild(b);
    });
  };
  async function ask2(text) {
    bmsg(text, 1);
    const w = bmsg('...');
    try {
      const r = await api('/assistant', { method: 'POST', body: { message: text } });
      w.textContent = r.reply;
      let list = r.chips && r.chips.length ? r.chips : DEF;
      if (r.created) {
        await load();
        if (V === 'dash' || V === 'tix') render();
        toast('Ticket #' + r.created.number + ' created');
        list = [['Open ticket', '', String(r.created.id)]].concat(list);
      }
      setChips(list);
    } catch (e) {
      w.textContent = 'Sorry, something went wrong. ' + e.message;
    }
    $('#bm').scrollTop = 1e9;
  }
  window.ask = ask2;
  $('#bform').onsubmit = e => { e.preventDefault(); const t = $('#bi').value.trim(); if (t) { $('#bi').value = ''; ask2(t); } };
  chips.onclick = e => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.t) { $('#bp').classList.add('hide'); return; }
    ask2(b.dataset.q);
  };
  let hello = false;
  $('#bf').onclick = () => {
    const p = $('#bp'); p.classList.toggle('hide');
    if (!p.classList.contains('hide')) {
      if (!hello) { hello = true; bmsg('Hi ' + U.name.split(' ')[0] + '! I can check your tickets or create one for you. What do you need?'); }
      $('#bi').focus();
    }
  };
  setChips(DEF);
})();

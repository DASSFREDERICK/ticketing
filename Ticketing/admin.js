/* Helix admin front-end: ticket timeline (date range + slider), manage tickets, manage users.
   Loaded after assistant.js. Only appears for users whose Zammad role is Admin (checked via /api/admin/me). */
(function () {
  const css = document.createElement('style');
  css.textContent = `
.nvl{padding:14px 14px 6px;font-size:10.5px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--mute)}
.lg{display:flex;gap:18px;align-items:center;color:var(--mute);margin:0 0 12px;flex-wrap:wrap}
.lg i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.fb{display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap;background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin-bottom:14px}
.fg{display:flex;flex-direction:column;gap:5px}
.fg>span{font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
.fg input,.fg select{width:auto;height:36px;padding:6px 10px}
.fb .bt{height:36px;padding:0 16px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;height:36px}
.seg button{border:0;border-right:1px solid var(--line);background:#fff;padding:0 14px;font-weight:500;color:var(--mute)}
.seg button:last-child{border-right:0}.seg button.on{background:var(--ink);color:#fff}
.rl{display:inline-block;padding:2px 12px;border-radius:99px;font-size:12px;font-weight:600}
.rl.admin{background:#ece6ff;color:#5b3fd1}.rl.agent{background:#dfeaff;color:#2563eb}.rl.customer{background:#eceff5;color:#5b6578}
.gt{background:#fff;border:1px solid var(--line);border-radius:14px;padding:12px 18px 18px;overflow-x:auto}
.gax,.gh,.gr{min-width:720px}
.gax{display:grid;grid-template-columns:230px 1fr;height:24px}
.gax div{position:relative;color:var(--mute);font-size:12px}
.gax span{position:absolute;top:2px;transform:translateX(-50%);white-space:nowrap}
.gh{font-weight:600;padding:16px 0 6px;border-bottom:1px solid var(--line)}
.gh small{color:var(--mute);font-weight:400}
.gr{display:grid;grid-template-columns:230px 1fr;align-items:center;height:38px;cursor:pointer;border-radius:6px}
.gr:hover{background:#f6f9ff}
.gl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-right:12px}
.gl .id{margin-right:6px}
.gk{position:relative;height:100%;border-left:1px solid var(--line)}
.gb{position:absolute;top:7px;height:24px;border-radius:6px;font-size:12px;line-height:22px;padding:0 8px;white-space:nowrap;overflow:hidden;min-width:30px}
.gb.c{background:#d9f5e5;border:1px solid #86d4a8;color:#16884a}
.gb.o{background:#fff0d2;border:1px dashed #e0a43a;color:#9a5f00}
.td{position:absolute;top:0;bottom:0;border-left:1.5px dashed #9aa5bd;pointer-events:none}
.okb{background:#d9f5e5;color:#16884a;border-radius:10px;padding:12px 14px;margin-top:14px;font-size:13.5px;overflow-wrap:anywhere}
.cu{grid-template-columns:1.2fr 1.7fr 100px 90px 170px}
.ac2{display:flex;gap:6px}
`;
  document.head.appendChild(css);

  I.shield = '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>';
  I.gantt = '<path d="M3 6h9M7 12h12M5 18h8"/>';
  I.user = '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>';

  const DAY = 864e5;
  let AD = [], AF = 'all', AC = 'All';
  let DF = '', DT = '';               // optional date filter (yyyy-mm-dd), blank = all time
  const dFmt = ms => new Date(ms).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  const ymd = ms => { const x = new Date(ms); return `${x.getFullYear()}-${d2(x.getMonth() + 1)}-${d2(x.getDate())}`; };
  const toLocal = d => { const x = new Date(d); return `${ymd(x)}T${d2(x.getHours())}:${d2(x.getMinutes())}`; };
  const midnight = ms => new Date(ms).setHours(0, 0, 0, 0);
  const isDone = t => grp(t.state) === 'done';
  const days = (a, b) => Math.max(1, Math.round((b - a) / DAY));

  // ---- route the new views through the existing render()
  const baseRender = render;
  render = function () {
    if (V === 'admin') return admView();
    if (V === 'adduser') return userView();
    return baseRender();
  };

  // ---- nav items (admins only)
  function addNav() {
    $('.sb .sp').insertAdjacentHTML('beforebegin',
      '<div class="nvl">Admin</div>' +
      `<button class="nv" data-v="admin" data-go="admin">${ic('gantt')}Ticket timeline</button>` +
      `<button class="nv" data-v="adduser" data-go="adduser">${ic('user')}Users</button>`);
    $('.bn').insertAdjacentHTML('beforeend',
      `<button data-v="admin" data-go="admin">${ic('shield', 22)}<span>Admin</span></button>`);
  }

  // ================= TIMELINE =================
  async function admView(refresh) {
    $('#view').innerHTML = '<div class="em">Loading...</div>';
    try { AD = await api('/admin/tickets' + (refresh ? '?refresh=1' : '')); }
    catch (e) { $('#view').innerHTML = `<div class="em">${esc(e.message)}</div>`; return; }
    if (V !== 'admin') return;
    draw();
  }

  function win() {
    const now = Date.now();
    let s = AD.length ? midnight(Math.min(...AD.map(t => +new Date(t.created)))) : midnight(now);
    let e = Math.max(now, ...AD.filter(t => t.closed).map(t => +new Date(t.closed))) + 2 * DAY;
    if (DF) s = +new Date(DF + 'T00:00');
    if (DT) e = +new Date(DT + 'T00:00') + DAY;
    if (e <= s) e = s + 7 * DAY;
    return { s, e };
  }

  function draw() {
    const now = Date.now();
    const open = AD.filter(t => !isDone(t)), closed = AD.filter(isDone);
    const hi = open.filter(t => PN(t.priority) === 'High').length;
    const avg = closed.length ? Math.round(closed.reduce((s, t) => s + days(+new Date(t.created), +new Date(t.closed)), 0) / closed.length) : 0;
    const S = [['Total Tickets', AD.length, 'All customers', 'b', 'list'], ['Open / In Progress', open.length, hi + ' high priority', 'g', 'clock'],
      ['Closed', closed.length, 'Completed', 'p', 'check'], ['Avg. time to close', avg + 'd', 'Closed tickets', 'o', 'alert']];
    const cats = [...new Set([...CATS, ...AD.map(t => t.category)])];
    const tabs = [['all', 'All', AD.length], ['open', 'Open', open.length], ['done', 'Closed', closed.length]];

    $('#view').innerHTML = `<h1 class="ph">Ticket timeline</h1><p class="sub">Every customer ticket, grouped by category, with how long it took or how long it has been open.</p>
<div class="stats">${S.map(s => `<div class="card sc"><div class="ico ${s[3]}">${ic(s[4], 24)}</div><div><span>${s[0]}</span><b>${s[1]}</b><em>${s[2]}</em></div></div>`).join('')}</div>
<div class="fb">
<div class="fg"><span>Status</span><div class="seg">${tabs.map(t => `<button class="${AF === t[0] ? 'on' : ''}" data-af="${t[0]}">${t[1]} (${t[2]})</button>`).join('')}</div></div>
<div class="fg"><span>Category</span><select id="ac"><option value="All">All categories</option>${cats.map(c => `<option ${AC === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></div>
<div class="fg"><span>From</span><input type="date" id="rf" value="${DF}" ${DT ? `max="${DT}"` : ''}></div>
<div class="fg"><span>To</span><input type="date" id="rt" value="${DT}" ${DF ? `min="${DF}"` : ''}></div>
<button class="bt" id="rr">Reset</button>
<span class="sp"></span><button class="bt" id="ar">Refresh</button><button class="bt p" data-go="adduser">Users</button></div>
<div class="lg"><span><i style="background:#86d4a8"></i>Closed</span><span><i style="background:#f3c77a"></i>Open (still in progress)</span><span>Today: ${dFmt(now)}</span></div>
<div class="gt" id="gt"></div>`;

    document.querySelectorAll('[data-af]').forEach(b => b.onclick = () => { AF = b.dataset.af; draw(); });
    $('#ac').onchange = e => { AC = e.target.value; draw(); };
    $('#ar').onclick = () => admView(true);
    $('#rf').onchange = e => { DF = e.target.value; $('#rt').min = DF; chart(); };
    $('#rt').onchange = e => { DT = e.target.value; $('#rf').max = DT; chart(); };
    $('#rr').onclick = () => { DF = DT = ''; draw(); };
    chart();
  }

  function chart() {
    const now = Date.now();
    const { s: start, e: end } = win(), span = end - start;
    const pct = ms => (ms - start) / span * 100;
    const inWin = t => { const a = +new Date(t.created), b = isDone(t) ? +new Date(t.closed) : now; return a < end && b >= start; };
    const L = AD.filter(t => (AF === 'all' || (AF === 'open' ? !isDone(t) : isDone(t))) && (AC === 'All' || t.category === AC) && inWin(t));

    const step = Math.max(1, Math.ceil(span / DAY / 6));
    let ticks = '';
    for (let d = 0; start + d * DAY < end; d += step) ticks += `<span style="left:${pct(start + d * DAY)}%">${dFmt(start + d * DAY)}</span>`;
    const tp = pct(now), today = tp >= 0 && tp <= 100 ? `<i class="td" style="left:${tp}%"></i>` : '';

    const cats = [...new Set([...CATS, ...AD.map(t => t.category)])];
    const body = cats.map(c => [c, L.filter(t => t.category === c)]).filter(g => g[1].length).map(([c, items]) => {
      const nc = items.filter(isDone).length;
      return `<div class="gh">${esc(c)} <small>· ${items.length} ticket${items.length === 1 ? '' : 's'}, ${nc} closed, ${items.length - nc} open</small></div>` +
        items.map(t => {
          const a = +new Date(t.created), done = isDone(t), b = done ? +new Date(t.closed) : now;
          const left = Math.max(0, pct(a)), right = Math.min(100, pct(b)), w = Math.max(right - left, 1.5), n = days(a, b);
          const lab = done ? n + 'd' : SL(t.state).toLowerCase() + ' · ' + n + 'd';
          return `<div class="gr" data-aid="${t.id}"><div class="gl"><span class="id">${esc(t.number)}</span>${esc(t.title)}</div>` +
            `<div class="gk">${today}<div class="gb ${done ? 'c' : 'o'}" style="left:${left}%;width:${w}%" title="${esc(t.title)}">${lab}</div></div></div>`;
        }).join('');
    }).join('');

    $('#gt').innerHTML = `<div class="gax"><div></div><div>${ticks}</div></div>${body || '<div class="em">No tickets in this date range</div>'}`;
    document.querySelectorAll('[data-aid]').forEach(r => r.onclick = () => detail(AD.find(t => String(t.id) === r.dataset.aid)));
  }

  // ================= MANAGE / CLOSE TICKET =================
  let AG = null;
  async function agents() {
    if (!AG) { try { AG = await api('/admin/agents'); } catch (e) { AG = []; } }
    return AG;
  }

  async function detail(t) {
    const a = +new Date(t.created), done = isDone(t), b = done ? +new Date(t.closed) : Date.now();
    const n = days(a, b), dl = n + ' day' + (n === 1 ? '' : 's');
    if (done) {   // closed tickets: read-only
      modal(`<h3>#${esc(t.number)} ${esc(t.title)}</h3><p class="sub">${esc(t.category)}</p>
<dl class="dl"><dt>Status</dt><dd>${stat(t.state)}</dd><dt>Priority</dt><dd>${pri(t.priority)}</dd>
<dt>Customer</dt><dd>${esc(t.customer || '-')}</dd><dt>Assigned to</dt><dd>${esc(own(t.owner))}</dd>
<dt>Created</dt><dd>${fdt(t.created)}</dd><dt>Closed</dt><dd>${fdt(t.closed)}</dd><dt>Time to close</dt><dd>${dl}</dd></dl>
<div class="ac"><button class="bt" data-close>Close</button></div>`);
      return;
    }
    const ag = await agents();
    const pk = String(t.priority).replace(/^\d\s*/, '').toLowerCase();
    const cur = ag.find(x => x.email === t.owner || x.login === t.owner);
    const now = toLocal(Date.now());
    modal(`<h3>#${esc(t.number)} Manage ticket</h3>
<p class="sub">${stat(t.state)} &nbsp; Customer: ${esc(t.customer || '-')} &nbsp; Created ${fdt(t.created)} &nbsp; Open for ${dl}</p>
<label for="e1" style="margin-top:8px">Subject</label><input id="e1" maxlength="120" value="${esc(t.title)}">
<div class="row2"><div><label for="e2">Category</label><select id="e2">${CATS.map(c => `<option ${c === t.category ? 'selected' : ''}>${c}</option>`).join('')}</select></div>
<div><label for="e3">Priority</label><select id="e3">${[['low', 'Low'], ['normal', 'Medium'], ['high', 'High']].map(p => `<option value="${p[0]}" ${p[0] === pk ? 'selected' : ''}>${p[1]}</option>`).join('')}</select></div></div>
<label for="e4">Assigned to</label><select id="e4"><option value="1">Unassigned</option>${ag.map(x => `<option value="${x.id}" ${cur && cur.id === x.id ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select>
<label for="e5">Closing date and time</label><input id="e5" type="datetime-local" value="${now}" min="${toLocal(t.created)}" max="${now}">
<label for="e6">Note to customer (optional)</label><textarea id="e6" rows="3" maxlength="1000" placeholder="e.g. Fixed by restarting the access point."></textarea>
<div class="err" id="ee"></div>
<div class="ac"><button class="bt" data-close>Cancel</button><button class="bt" id="esv">Save changes</button><button class="bt r" id="ecl">Close case</button></div>`);

    const send = async close => {
      const title = $('#e1').value.trim();
      if (!title) { $('#ee').textContent = 'Add a subject.'; return; }
      const body = { title, category: $('#e2').value, priority: $('#e3').value, owner_id: +$('#e4').value, note: $('#e6').value.trim(), close };
      if (close) {
        const v = $('#e5').value;
        if (!v) { $('#ee').textContent = 'Pick the closing date.'; return; }
        const d = new Date(v);
        if (d < new Date(t.created)) { $('#ee').textContent = 'Closing date cannot be before the ticket was created.'; return; }
        body.closed_at = d.toISOString();
        if (!confirm('Close ticket #' + t.number + '? The customer will not be able to reply afterwards.')) return;
      }
      $('#esv').disabled = $('#ecl').disabled = true; $('#ee').textContent = '';
      try {
        const r = await api('/admin/tickets/' + t.id, { method: 'PUT', body });
        closeM();
        toast(!close ? 'Changes saved' : r.close_at_applied === false ? `Ticket #${t.number} closed (Zammad kept the real close time)` : `Ticket #${t.number} closed`);
        admView(true);
      } catch (x) { $('#ee').textContent = x.message; $('#esv').disabled = $('#ecl').disabled = false; }
    };
    $('#esv').onclick = () => send(false);
    $('#ecl').onclick = () => send(true);
  }

  // ================= USERS =================
  let AU = [], UQ = '', UR = 'all';
  const RL = { admin: 'Admin', agent: 'Agent', customer: 'Customer' };

  async function userView() {
    $('#view').innerHTML = '<div class="em">Loading...</div>';
    try { AU = await api('/admin/users'); }
    catch (e) { $('#view').innerHTML = `<div class="em">${esc(e.message)}</div>`; return; }
    if (V !== 'adduser') return;
    $('#view').innerHTML = `<h1 class="ph">Users</h1><p class="sub">All accounts in the helpdesk. Customer accounts can be added, edited or removed here; admins and agents are managed in Zammad.</p>
<div class="fb"><div class="fg"><span>Role</span><div class="seg" id="ur"></div></div>
<div class="fg"><span>Search</span><input id="uq" placeholder="Name or email" value="${esc(UQ)}" style="min-width:240px"></div>
<span class="sp"></span><button class="bt" data-go="admin">Back to timeline</button><button class="bt p" id="ua">Add user</button></div>
<div id="ul"></div>`;
    $('#uq').oninput = e => { UQ = e.target.value; ulist(); };
    $('#ua').onclick = () => userForm(null);
    $('#ur').onclick = e => { const b = e.target.closest('[data-ur]'); if (b) { UR = b.dataset.ur; ulist(); } };
    $('#ul').onclick = e => {
      const ed = e.target.closest('[data-ue]'), rm = e.target.closest('[data-ud]');
      if (ed) userForm(AU.find(u => String(u.id) === ed.dataset.ue));
      if (rm) delUser(AU.find(u => String(u.id) === rm.dataset.ud));
    };
    ulist();
  }

  function ulist() {
    const q = UQ.trim().toLowerCase();
    const cnt = r => r === 'all' ? AU.length : AU.filter(u => u.role === r).length;
    $('#ur').innerHTML = [['all', 'All'], ['customer', 'Customers'], ['agent', 'Agents'], ['admin', 'Admins']]
      .map(r => `<button class="${UR === r[0] ? 'on' : ''}" data-ur="${r[0]}">${r[1]} (${cnt(r[0])})</button>`).join('');
    const L = AU.filter(u => (UR === 'all' || u.role === UR) && (!q || (u.name + ' ' + u.email).toLowerCase().includes(q)));
    $('#ul').innerHTML = `<div class="tbl"><div class="r h cu"><span>Name</span><span>Email</span><span>Role</span><span>Status</span><span>Actions</span></div>` +
      (L.length ? L.map(u => `<div class="r cu"><span class="t">${esc(u.name)}</span><span class="dt">${esc(u.email)}</span>
<span><span class="rl ${u.role}">${RL[u.role]}</span></span>
<span>${u.active ? '<span class="st d">Active</span>' : '<span class="st p">Inactive</span>'}</span>
${u.role === 'customer' ? `<span class="ac2"><button class="bt s" data-ue="${u.id}">Edit</button><button class="bt s" data-ud="${u.id}" style="color:var(--red)">Remove</button></span>` : '<span class="dt">Managed in Zammad</span>'}</div>`).join('')
        : '<div class="em">No users found</div>') + `</div><p class="sub" style="margin-top:10px">${L.length} of ${AU.length} users</p>`;
  }

  async function reloadUsers() { try { AU = await api('/admin/users'); } catch (e) { } if (V === 'adduser') ulist(); }

  function userForm(u) {
    const add = !u;
    modal(`<h3>${add ? 'Add user' : 'Edit user'}</h3><p class="sub">${add ? 'Creates a customer account in Zammad.' : esc(u.email)}</p>
<div class="row2"><div><label for="f1" style="margin-top:0">First name <span class="req">*</span></label><input id="f1" autocomplete="off" value="${add ? '' : esc(u.firstname)}"></div>
<div><label for="f2" style="margin-top:0">Last name</label><input id="f2" autocomplete="off" value="${add ? '' : esc(u.lastname)}"></div></div>
${add ? '<label for="f3">Email <span class="req">*</span></label><input id="f3" type="email" autocomplete="off">' : ''}
<label for="f4">${add ? 'Password <span class="req">*</span>' : 'New password'}</label><input id="f4" type="text" autocomplete="new-password" placeholder="${add ? 'At least 8 characters' : 'Leave blank to keep the current one'}">
<div class="err" id="fe"></div><div id="fo"></div>
<div class="ac"><button type="button" class="bt" data-close id="fc">Cancel</button><button type="button" class="bt" id="fg">Generate password</button><button type="button" class="bt p" id="fs">${add ? 'Create user' : 'Save'}</button></div>`);
    $('#fg').onclick = () => {
      const c = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789', r = crypto.getRandomValues(new Uint32Array(12));
      $('#f4').value = Array.from(r, x => c[x % c.length]).join('');
    };
    let busy = false;
    $('#fs').onclick = async () => {
      if (busy) return;
      const body = { firstname: $('#f1').value.trim(), lastname: $('#f2').value.trim(), password: $('#f4').value };
      if (add) body.email = $('#f3').value.trim();
      busy = true; $('#fs').disabled = true; $('#fe').textContent = '';
      try {
        if (add) {
          const r = await api('/admin/users', { method: 'POST', body });
          $('#fo').innerHTML = `<div class="okb"><b>User created.</b><br>Email: ${esc(r.email)}<br>Password: ${esc(body.password)}</div>`;
          ['#f1', '#f2', '#f3', '#f4'].forEach(s => $(s).value = '');
          $('#fc').textContent = 'Close'; toast('User ' + r.email + ' created');
        } else {
          await api('/admin/users/' + u.id, { method: 'PUT', body });
          closeM(); toast('User updated');
        }
        reloadUsers();
      } catch (x) { $('#fe').textContent = x.message; }
      busy = false; $('#fs').disabled = false;
    };
  }

  async function delUser(u) {
    if (!u || !confirm(`Remove ${u.name} (${u.email})? They will no longer be able to sign in.`)) return;
    try {
      const r = await api('/admin/users/' + u.id, { method: 'DELETE' });
      toast(r.deactivated ? u.name + ' has tickets, so the account was deactivated instead' : u.name + ' removed');
      reloadUsers();
    } catch (e) { toast(e.message); }
  }

  // ---- reveal admin UI once the user is signed in and confirmed admin
  let checked = false;
  const iv = setInterval(async () => {
    if (checked || typeof U === 'undefined' || !U || !U.email) return;
    checked = true; clearInterval(iv);
    try { if ((await api('/admin/me')).admin) addNav(); } catch (e) { }
  }, 300);
})();

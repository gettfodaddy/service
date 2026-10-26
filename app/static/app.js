const $ = (id) => document.getElementById(id);
let csrf = '';
let tariffs = [];
let squads = [];
let overview = null;
let customers = [];
let orders = [];
let tickets = [];
let botContent = null;
let activeView = 'overview';

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function money(kopeks) { return new Intl.NumberFormat('ru-RU', {style:'currency',currency:'RUB',maximumFractionDigits:2}).format(kopeks / 100); }
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; setTimeout(() => $('toast').hidden = true, 4500); }
async function request(path, options = {}) {
  const response = await fetch(path, {credentials:'same-origin', headers:{'Content-Type':'application/json', ...(csrf ? {'X-CSRF-Token':csrf} : {})}, ...options});
  let data = null;
  try { data = await response.json(); } catch {}
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') showLogin();
    const detail = data?.detail;
    throw new Error(typeof detail === 'string' ? detail : 'Не удалось выполнить действие');
  }
  return data;
}
function showLogin() { $('loginView').hidden = false; $('appView').hidden = true; }
function showApp() { $('loginView').hidden = true; $('appView').hidden = false; }

async function loadData() {
  [overview, tariffs, squads, customers, orders, tickets, botContent] = await Promise.all([
    request('/api/overview'), request('/api/tariffs'), request('/api/squads'),
    request('/api/customers'), request('/api/orders'), request('/api/tickets'), request('/api/bot-content')
  ]);
  renderOverview(); renderTariffs(); renderSquads(); renderEvents(); renderCustomers(); renderOrders(); renderTickets(); renderBotContent();
}
function goto(view) {
  activeView = view;
  const labels = {overview:['Обзор','Добро пожаловать','Управляйте сервисом из одного пространства.'], tariffs:['Тарифы','Тарифы','Настраивайте цены и сквады.'], bot:['Бот','Редактор бота','Настраивайте меню и тексты без изменения кода.'], squads:['Сквады','Сквады Remnawave','Назначайте внутренние сквады тарифам.'], customers:['Клиенты','Клиенты','Пользователи нового бота.'], orders:['Заказы','Заказы','Платежи и активации подписок.'], tickets:['Тикеты','Поддержка','Отвечайте клиентам из панели.'], activity:['Журнал','История изменений','Каждое действие администратора сохраняется.']};
  for (const name of Object.keys(labels)) $(name + 'Page').hidden = name !== view;
  document.querySelectorAll('.nav-item').forEach(node => node.classList.toggle('active',node.dataset.view === view));
  $('breadcrumb').textContent = labels[view][0]; $('pageTitle').textContent = labels[view][1]; $('pageSubtitle').textContent = labels[view][2];
  $('headingAction').hidden = view !== 'tariffs';
}
function renderOverview() {
  $('countTariffs').textContent = overview?.tariffs ?? 0;
  $('countPublished').textContent = overview?.published ?? 0;
  $('countSquads').textContent = overview?.squads ?? 0;
}
function renderEvents() {
  const rows = (overview?.events || []).map(event => `<div class="event"><span><b>${escapeHtml(event.action)}</b> · ${escapeHtml(event.detail)}</span><time>${new Date(event.created_at).toLocaleString('ru-RU')}</time></div>`).join('') || 'Пока нет действий';
  $('overviewEvents').innerHTML = rows; $('allEvents').innerHTML = rows;
}
function renderTariffs() {
  const query = $('tariffSearch').value.toLocaleLowerCase('ru');
  const filtered = tariffs.filter(t => t.name.toLocaleLowerCase('ru').includes(query));
  $('tariffCount').textContent = `${filtered.length} тарифов`;
  $('tariffCards').innerHTML = filtered.map(t => {
    const from = t.periods.length ? Math.min(...t.periods.map(p => p.price_kopeks)) : 0;
    const traffic = t.traffic_gb ? `${t.traffic_gb} ГБ` : 'Безлимит';
    return `<article class="tariff-card"><div class="tariff-top"><div class="tariff-symbol">${t.is_trial ? '◇' : '✦'}</div><span class="badge ${t.published ? 'published' : 'draft'}">${t.published ? 'Опубликован' : 'Черновик'}</span></div><h3>${escapeHtml(t.name)}</h3><p>${escapeHtml(t.description || 'Описание не добавлено')}</p><div class="tariff-metrics"><span>◈ ${escapeHtml(traffic)}</span><span>▣ ${t.devices} устр.</span><span>◎ ${t.squad_uuids.length} скв.</span></div><div class="tariff-footer"><strong>${t.is_trial ? 'Бесплатно' : 'от ' + money(from)}</strong><button data-edit="${t.id}">Настроить ↗</button></div></article>`;
  }).join('') || '<div class="surface empty">Тарифы не найдены. Создайте первый тариф.</div>';
}
function renderSquads() {
  $('squadRows').innerHTML = squads.map(s => `<div class="squad-row ${s.present ? '' : 'missing'}"><div><strong>${escapeHtml(s.name)}</strong><small>${escapeHtml(s.uuid)}</small></div><span class="status">${s.present ? '● Доступен' : '⚠ Нет в панели'}</span></div>`).join('') || '<p class="empty">Список пуст. Нажмите «Синхронизировать».</p>';
}
function renderCustomers() {
  $('customerRows').innerHTML = customers.map(c => `<div class="squad-row"><div><strong>Telegram ${c.telegram_id}</strong><small>${escapeHtml(c.remna_username || 'Без подписки')} · ${c.trial_used ? 'триал использован' : 'триал доступен'}${c.referred_by ? ` · приглашён ${c.referred_by}` : ''}</small></div><span class="status">${money(c.balance_kopeks)}</span></div>`).join('') || '<p class="empty">Клиентов пока нет.</p>';
}
function renderOrders() {
  $('orderRows').innerHTML = orders.map(o => `<div class="squad-row"><div><strong>Заказ №${o.id} · Telegram ${o.customer_id}</strong><small>${o.days} дней · бонусы ${money(o.bonus_kopeks)} · ЮKassa ${money(o.cash_kopeks)}</small></div><span class="status">${escapeHtml(o.status)}</span></div>`).join('') || '<p class="empty">Заказов пока нет.</p>';
}
function renderTickets() {
  $('ticketRows').innerHTML = tickets.map(t => `<div class="squad-row"><div><strong>Тикет №${t.id} · Telegram ${t.customer_id}</strong><small>${new Date(t.created_at).toLocaleString('ru-RU')}</small></div><button class="secondary" data-ticket="${t.id}">${escapeHtml(t.status)} ↗</button></div>`).join('') || '<p class="empty">Обращений пока нет.</p>';
}
async function openTicket(id) {
  const ticket = await request(`/api/tickets/${id}`);
  $('ticketDetail').innerHTML = `<div class="ticket-detail"><h3>Тикет №${id}</h3>${ticket.messages.map(m => `<div class="ticket-message"><strong>${m.sender_id === ticket.customer_id ? 'Клиент' : 'Поддержка'}</strong><p>${escapeHtml(m.text)}</p></div>`).join('')}<form id="ticketReplyForm"><textarea id="ticketReplyText" rows="4" maxlength="4000" placeholder="Ответ клиенту" required></textarea><button class="primary" type="submit">Отправить ответ</button></form></div>`;
  $('ticketReplyForm').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const result = await request(`/api/tickets/${id}/reply`, {method:'POST', body:JSON.stringify({text:$('ticketReplyText').value.trim()})});
      await loadData(); await openTicket(id); toast(result.delivered ? 'Ответ сохранён и отправлен' : 'Ответ сохранён, Telegram не доставил сообщение');
    } catch (error) { toast(error.message); }
  });
}
function orderedBotButtons() { return [...(botContent?.buttons || [])].sort((a,b) => a.position - b.position); }
function renderBotPreview() {
  if (!botContent) return;
  const welcome = botContent.texts.find(t => t.key === 'welcome')?.body || '';
  const brand = botContent.texts.find(t => t.key === 'brand_name')?.body || $('brandName').textContent;
  $('botPreviewText').textContent = welcome.replaceAll('{app_name}', brand).replaceAll('{{','{').replaceAll('}}','}');
  $('botPreviewButtons').innerHTML = orderedBotButtons().filter(b => b.visible).map(b => `<span>${escapeHtml(b.label)}</span>`).join('');
}
function renderBotContent() {
  if (!botContent) return;
  $('botButtonRows').innerHTML = orderedBotButtons().map((b,index) => `<div class="bot-button-row" data-action="${escapeHtml(b.action)}"><div class="bot-button-order"><button type="button" data-move="up" ${index === 0 ? 'disabled' : ''} aria-label="Вверх">↑</button><button type="button" data-move="down" ${index === botContent.buttons.length-1 ? 'disabled' : ''} aria-label="Вниз">↓</button></div><label><span>${escapeHtml(b.action)}</span><input data-bot-label maxlength="64" value="${escapeHtml(b.label)}" aria-label="Подпись кнопки ${escapeHtml(b.action)}"></label><label class="bot-visible"><input type="checkbox" data-bot-visible ${b.visible ? 'checked' : ''}> Показывать</label></div>`).join('');
  $('botLabelRows').innerHTML = botContent.labels.map(item => `<label class="bot-small-label" data-key="${escapeHtml(item.key)}"><strong>${escapeHtml(item.title)}</strong><input data-ui-label maxlength="64" value="${escapeHtml(item.label)}"><small>${item.variables.length ? 'Переменные: ' + item.variables.map(v => '{'+escapeHtml(v)+'}').join(', ') : 'Обычная подпись'}</small></label>`).join('');
  const textField = t => `<label class="bot-text-row" data-key="${escapeHtml(t.key)}"><strong>${escapeHtml(t.title)}</strong><textarea data-bot-text rows="${t.max_length === 180 ? 2 : 5}" maxlength="${t.max_length}">${escapeHtml(t.body)}</textarea><small>${t.variables.length ? 'Переменные: ' + t.variables.map(v => '{'+escapeHtml(v)+'}').join(', ') : 'Переменных нет'} · до ${t.max_length} символов</small></label>`;
  $('botTextRows').innerHTML = botContent.texts.filter(t => t.group !== 'system').map(textField).join('');
  $('botSystemTextRows').innerHTML = botContent.texts.filter(t => t.group === 'system').map(textField).join('');
  renderBotPreview();
}
function moveBotButton(action, direction) {
  const buttons = orderedBotButtons();
  const index = buttons.findIndex(b => b.action === action);
  const other = buttons[index + (direction === 'up' ? -1 : 1)];
  if (!other) return;
  [buttons[index].position, other.position] = [other.position, buttons[index].position];
  renderBotContent();
}
async function saveBotContent() {
  if (!botContent) return;
  try {
    await request('/api/bot-content', {method:'PUT', body:JSON.stringify({
      texts:Object.fromEntries(botContent.texts.map(t => [t.key,t.body])),
      labels:Object.fromEntries(botContent.labels.map(item => [item.key,item.label])),
      buttons:botContent.buttons.map(b => ({action:b.action,label:b.label,position:b.position,visible:b.visible}))
    })});
    $('brandName').textContent = botContent.texts.find(t => t.key === 'brand_name')?.body || $('brandName').textContent;
    document.title = `${$('brandName').textContent} — управление сервисом`;
    toast('Меню и тексты сохранены. Новые сообщения в боте уже используют их.');
  } catch (error) { toast(error.message); }
}
function parsePeriods(value) {
  const parts = value.split(',').map(x => x.trim()).filter(Boolean);
  if (!parts.length) throw new Error('Добавьте хотя бы один период');
  return parts.map(part => {
    const match = part.match(/^(\d+)\s*:\s*(\d+(?:[.,]\d{1,2})?)$/);
    if (!match) throw new Error('Формат цены: 30:299, 90:799');
    return {days:Number(match[1]),price_kopeks:Math.round(Number(match[2].replace(',','.'))*100)};
  });
}
function openDrawer(tariff = null) {
  $('tariffDrawer').hidden = false; $('drawerBackdrop').hidden = false;
  $('drawerTitle').textContent = tariff ? `Тариф «${tariff.name}»` : 'Новый тариф';
  $('tariffId').value = tariff?.id || '';
  $('fieldName').value = tariff?.name || '';
  $('fieldDescription').value = tariff?.description || '';
  $('fieldTraffic').value = tariff?.traffic_gb ?? 100;
  $('fieldDevices').value = tariff?.devices ?? 1;
  $('fieldOrder').value = tariff?.sort_order ?? 0;
  $('fieldPublished').checked = tariff?.published || false;
  $('fieldPeriods').value = tariff?.periods.map(p => `${p.days}:${p.price_kopeks / 100}`).join(', ') || '';
  $('fieldPeriods').disabled = tariff?.is_trial || false;
  $('fieldSquads').innerHTML = squads.filter(s => s.present || tariff?.squad_uuids.includes(s.uuid)).map(s => `<label class="squad-choice"><input type="checkbox" value="${escapeHtml(s.uuid)}" ${tariff?.squad_uuids.includes(s.uuid) ? 'checked' : ''} ${s.present ? '' : 'disabled'}><span>${escapeHtml(s.name)}${s.present ? '' : ' (нет в панели)'}</span></label>`).join('') || '<p class="empty">Сначала синхронизируйте сквады.</p>';
}
function closeDrawer() { $('tariffDrawer').hidden = true; $('drawerBackdrop').hidden = true; }
async function saveTariff(event) {
  event.preventDefault();
  const id = $('tariffId').value;
  const existing = tariffs.find(t => String(t.id) === id);
  try {
    const payload = {
      name:$('fieldName').value.trim(),description:$('fieldDescription').value.trim(),
      traffic_gb:Number($('fieldTraffic').value),devices:Number($('fieldDevices').value),
      sort_order:Number($('fieldOrder').value),published:$('fieldPublished').checked,
      is_trial:existing?.is_trial || false,
      periods:existing?.is_trial ? existing.periods : parsePeriods($('fieldPeriods').value),
      squad_uuids:[...$('fieldSquads').querySelectorAll('input:checked')].map(x => x.value)
    };
    await request(id ? `/api/tariffs/${id}` : '/api/tariffs', {method:id ? 'PUT' : 'POST',body:JSON.stringify(payload)});
    closeDrawer(); await loadData(); toast('Тариф сохранён');
  } catch (error) { toast(error.message); }
}

$('loginForm').addEventListener('submit', async event => {
  event.preventDefault(); $('loginError').textContent = '';
  try { const result = await request('/api/login', {method:'POST',body:JSON.stringify({code:$('loginCode').value})}); csrf = result.csrf_token; $('loginCode').value=''; showApp(); await loadData(); }
  catch (error) { $('loginError').textContent = error.message; }
});
$('logoutBtn').addEventListener('click', async () => { try { await request('/api/logout',{method:'POST'}); } catch {} csrf=''; showLogin(); });
document.querySelectorAll('[data-view]').forEach(node => node.addEventListener('click', () => goto(node.dataset.view)));
document.querySelectorAll('[data-goto]').forEach(node => node.addEventListener('click', () => goto(node.dataset.goto)));
$('headingAction').addEventListener('click', () => openDrawer()); $('heroCreate').addEventListener('click', () => { goto('tariffs'); openDrawer(); });
$('tariffSearch').addEventListener('input', renderTariffs);
$('tariffCards').addEventListener('click', event => { const button=event.target.closest('[data-edit]'); if(button) openDrawer(tariffs.find(t => t.id === Number(button.dataset.edit))); });
$('drawerClose').addEventListener('click', closeDrawer); $('drawerBackdrop').addEventListener('click', closeDrawer); $('cancelEdit').addEventListener('click', closeDrawer);
$('tariffForm').addEventListener('submit', saveTariff);
$('syncSquadsBtn').addEventListener('click', async () => { try { const result=await request('/api/squads/sync',{method:'POST'}); await loadData(); toast(`Получено сквадов: ${result.count}`); } catch(error) { toast(error.message); } });
$('ticketRows').addEventListener('click', async event => { const button=event.target.closest('[data-ticket]'); if(button) { try { await openTicket(button.dataset.ticket); } catch(error) { toast(error.message); } } });
$('botButtonRows').addEventListener('click', event => { const move=event.target.closest('[data-move]'); if(move) moveBotButton(move.closest('[data-action]').dataset.action, move.dataset.move); });
$('botButtonRows').addEventListener('input', event => { const row=event.target.closest('[data-action]'); if(!row) return; const button=botContent.buttons.find(b => b.action === row.dataset.action); if(event.target.matches('[data-bot-label]')) button.label=event.target.value; if(event.target.matches('[data-bot-visible]')) button.visible=event.target.checked; renderBotPreview(); });
$('botLabelRows').addEventListener('input', event => { const row=event.target.closest('[data-key]'); if(row) botContent.labels.find(item => item.key === row.dataset.key).label=event.target.value; });
$('botTextRows').addEventListener('input', event => { const row=event.target.closest('[data-key]'); if(!row) return; botContent.texts.find(t => t.key === row.dataset.key).body=event.target.value; renderBotPreview(); });
$('botSystemTextRows').addEventListener('input', event => { const row=event.target.closest('[data-key]'); if(row) botContent.texts.find(t => t.key === row.dataset.key).body=event.target.value; });
$('saveBotContent').addEventListener('click', saveBotContent);
$('resetBotContent').addEventListener('click', () => { if(!botContent) return; botContent.texts.forEach(t => t.body=t.default); botContent.buttons.forEach((b,index) => { b.label=b.default; b.position=index; b.visible=true; }); botContent.labels.forEach(item => item.label=item.default); renderBotContent(); toast('Исходные значения подготовлены. Нажмите «Сохранить изменения», чтобы применить их.'); });
document.addEventListener('keydown', event => { if(event.key === 'Escape') closeDrawer(); });
(async () => {
  try { const config = await request('/api/public-config'); $('brandName').textContent = config.app_name; document.title = `${config.app_name} — управление сервисом`; } catch {}
  try { const result=await request('/api/me'); csrf=result.csrf_token; $('adminId').textContent=`Администратор · ${result.admin_id}`; showApp(); await loadData(); goto('overview'); } catch { showLogin(); }
})();

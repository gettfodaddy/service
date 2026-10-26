const $ = (id) => document.getElementById(id);
let csrf = '';
let tariffs = [];
let squads = [];
let overview = null;
let customers = [];
let orders = [];
let tickets = [];
let botContent = null;
let activityData = {items:[],total:0,page:1,limit:30};
let activityPage = 1;
let ticketFilter = 'all';
let activeTicketId = null;
let activeView = 'overview';

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function icon(name) { return `<svg class="icon" aria-hidden="true"><use href="/assets/icons.svg#${name}"/></svg>`; }
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
  [overview, tariffs, squads, customers, orders, tickets, botContent, activityData] = await Promise.all([
    request('/api/overview'), request('/api/tariffs'), request('/api/squads'),
    request('/api/customers'), request('/api/orders'), request('/api/tickets'), request('/api/bot-content'),
    request(`/api/activity?category=${encodeURIComponent($('activityCategory').value)}&page=${activityPage}`)
  ]);
  renderOverview(); renderTariffs(); renderSquads(); renderEvents(); renderCustomers(); renderOrders(); renderTickets(); renderBotContent();
}
function goto(view) {
  activeView = view;
  const labels = {overview:['Обзор','Добро пожаловать','Управляйте сервисом из одного пространства.'], tariffs:['Тарифы','Тарифы','Настраивайте цены и сквады.'], bot:['Бот','Редактор бота','Настраивайте меню и тексты без изменения кода.'], squads:['Сквады','Сквады Remnawave','Назначайте внутренние сквады тарифам.'], customers:['Клиенты','Клиенты','Пользователи нового бота.'], orders:['Заказы','Заказы','Платежи и активации подписок.'], tickets:['Тикеты','Поддержка','Переписка с клиентами и статусы обращений.'], activity:['Журнал','Журнал событий','Операции с тарифами, заказами, оплатами и поддержкой.']};
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
  const names = {'ticket.created':'Новое обращение','ticket.customer_replied':'Ответ клиента','ticket.replied':'Ответ поддержки','ticket.status_changed':'Статус обращения изменён','ticket.delivery_failed':'Ошибка доставки в Telegram','squads.synced':'Сквады обновлены','bot.content.updated':'Настройки бота изменены','tariff.created':'Тариф создан','tariff.updated':'Тариф обновлён','order.created':'Заказ создан','order.completed':'Подписка активирована','order.canceled':'Заказ отменён','order.referral_credited':'Реферальный бонус начислен','payment.created':'Платёж создан','payment.confirmed':'Оплата подтверждена','payment.canceled':'Оплата отменена','payment.failed':'Ошибка создания платежа','payment.reconciliation_failed':'Ошибка сверки оплаты','trial.activated':'Пробный доступ выдан','trial.failed':'Ошибка пробного доступа','auth.login':'Вход администратора','auth.logout':'Выход администратора'};
  const row = event => `<div class="activity-event"><span class="activity-dot ${escapeHtml(event.action.split('.')[0])}"></span><div class="activity-body"><strong>${escapeHtml(names[event.action] || event.action.replaceAll('.', ' · '))}</strong><p>${escapeHtml(event.detail || (event.entity_id ? 'Объект №' + event.entity_id : 'Действие выполнено'))}</p><small>${event.actor_id ? 'Администратор · ' + event.actor_id : 'Система'}${event.entity_id ? ' · №' + event.entity_id : ''}</small></div><time title="${escapeHtml(event.created_at)}">${new Date(event.created_at).toLocaleString('ru-RU')}</time></div>`;
  $('overviewEvents').innerHTML = (overview?.events || []).map(row).join('') || '<p class="empty">Пока нет событий.</p>';
  $('allEvents').innerHTML = (activityData.items || []).map(row).join('') || '<p class="empty">В этой категории событий нет.</p>';
  $('activityTotal').textContent = `${activityData.total} событий`;
  $('activityPageNumber').textContent = activityData.page;
  $('activityPrev').disabled = activityData.page <= 1;
  $('activityNext').disabled = activityData.page * activityData.limit >= activityData.total;
}
function renderTariffs() {
  const query = $('tariffSearch').value.toLocaleLowerCase('ru');
  const filtered = tariffs.filter(t => t.name.toLocaleLowerCase('ru').includes(query));
  $('tariffCount').textContent = `${filtered.length} тарифов`;
  $('tariffCards').innerHTML = filtered.map(t => {
    const from = t.periods.length ? Math.min(...t.periods.map(p => p.price_kopeks)) : 0;
    const traffic = t.traffic_gb ? `${t.traffic_gb} ГБ` : 'Безлимит';
    return `<article class="tariff-card"><div class="tariff-top"><div class="tariff-symbol">${icon(t.is_trial ? 'sparkles' : 'layers')}</div><span class="badge ${t.published ? 'published' : 'draft'}">${t.published ? 'Опубликован' : 'Черновик'}</span></div><h3>${escapeHtml(t.name)}</h3><p>${escapeHtml(t.description || 'Описание не добавлено')}</p><div class="tariff-metrics"><span>${icon('network')} ${escapeHtml(traffic)}</span><span>${icon('users')} ${t.devices} устр.</span><span>${icon('layers')} ${t.squad_uuids.length} скв.</span></div><div class="tariff-footer"><strong>${t.is_trial ? 'Бесплатно' : 'от ' + money(from)}</strong><button data-edit="${t.id}">Настроить ↗</button></div></article>`;
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
  const query = $('ticketSearch').value.trim().toLowerCase();
  const filtered = tickets.filter(t => (ticketFilter === 'all' || t.status === ticketFilter) && (`${t.id} ${t.customer_id}`).includes(query));
  const labels = {open:'Новый',answered:'Отвечен',closed:'Закрыт'};
  for (const status of ['All','Open','Answered','Closed']) $('ticketCount' + status).textContent = status === 'All' ? tickets.length : tickets.filter(t => t.status === status.toLowerCase()).length;
  document.querySelectorAll('[data-ticket-filter]').forEach(node => node.classList.toggle('active', node.dataset.ticketFilter === ticketFilter));
  $('ticketRows').innerHTML = filtered.map(t => `<button class="ticket-row ${activeTicketId === t.id ? 'selected' : ''}" data-ticket="${t.id}"><span class="ticket-row-top"><strong>Обращение #${t.id}</strong><span class="ticket-status ${escapeHtml(t.status)}">${labels[t.status] || escapeHtml(t.status)}</span></span><span class="ticket-row-user">Telegram ${t.customer_id}</span><span class="ticket-row-preview">${escapeHtml(t.preview || 'Сообщений пока нет')}</span><time>${new Date(t.updated_at).toLocaleString('ru-RU')}</time></button>`).join('') || '<p class="empty">По этому фильтру обращений нет.</p>';
}
async function openTicket(id) {
  const ticket = await request(`/api/tickets/${id}`);
  activeTicketId = ticket.id; renderTickets();
  const statusLabels = {open:'Новое',answered:'Отвечено',closed:'Закрыто'};
  $('ticketDetail').innerHTML = `<div class="ticket-conversation-head"><div><span class="eyebrow">ОБРАЩЕНИЕ #${ticket.id}</span><h2>Диалог с клиентом</h2><p>Telegram ID ${ticket.customer_id} · создан ${new Date(ticket.created_at).toLocaleString('ru-RU')}</p></div><select id="ticketStatus" class="control-input" aria-label="Статус обращения"><option value="open" ${ticket.status === 'open' ? 'selected' : ''}>Новое</option><option value="answered" ${ticket.status === 'answered' ? 'selected' : ''}>Отвечено</option><option value="closed" ${ticket.status === 'closed' ? 'selected' : ''}>Закрыто</option></select></div><div class="ticket-messages">${ticket.messages.map(m => `<div class="ticket-bubble ${m.sender_id === ticket.customer_id ? 'from-customer' : 'from-support'}"><div class="ticket-bubble-meta"><strong>${m.sender_id === ticket.customer_id ? 'Клиент' : 'Поддержка'}</strong><time>${new Date(m.created_at).toLocaleString('ru-RU')}</time></div><p>${escapeHtml(m.text)}</p></div>`).join('') || '<p class="empty">Сообщений пока нет.</p>'}</div><form id="ticketReplyForm" class="ticket-composer"><label for="ticketReplyText">Ответ клиенту</label><textarea id="ticketReplyText" rows="4" maxlength="4000" placeholder="Напишите сообщение…" required></textarea><div class="ticket-composer-bottom"><small>Ответ придёт клиенту в Telegram. ${statusLabels[ticket.status] || ''}</small><button class="primary" type="submit"><svg class="icon"><use href="/assets/icons.svg#send"/></svg> Отправить</button></div></form>`;
  $('ticketDetail').querySelector('.ticket-messages').scrollTop = $('ticketDetail').querySelector('.ticket-messages').scrollHeight;
  $('ticketStatus').addEventListener('change', async event => { try { await request(`/api/tickets/${id}/status`, {method:'POST', body:JSON.stringify({status:event.target.value})}); await loadData(); await openTicket(id); toast('Статус обращения обновлён'); } catch (error) { toast(error.message); } });
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
  $('botPreviewButtons').innerHTML = orderedBotButtons().filter(b => b.visible).map(b => `<span class="${escapeHtml(b.style || '')}">${b.icon_custom_emoji_id ? '<span class="preview-emoji">✦</span> ' : ''}${escapeHtml(b.label)}</span>`).join('');
}
function renderBotContent() {
  if (!botContent) return;
  $('botButtonRows').innerHTML = orderedBotButtons().map((b,index) => `<div class="bot-button-row" data-action="${escapeHtml(b.action)}"><div class="bot-button-order"><button type="button" data-move="up" ${index === 0 ? 'disabled' : ''} aria-label="Вверх">↑</button><button type="button" data-move="down" ${index === botContent.buttons.length-1 ? 'disabled' : ''} aria-label="Вниз">↓</button></div><div class="bot-button-fields"><label><span>Подпись · ${escapeHtml(b.action)}</span><input data-bot-label maxlength="64" value="${escapeHtml(b.label)}" aria-label="Подпись кнопки ${escapeHtml(b.action)}"></label><label><span>Telegram custom emoji ID</span><input data-bot-icon inputmode="numeric" maxlength="30" placeholder="Например, 5453969572354878595" value="${escapeHtml(b.icon_custom_emoji_id || '')}"></label><label><span>Цвет кнопки</span><select data-bot-style><option value="" ${!b.style ? 'selected' : ''}>Стандартный</option><option value="primary" ${b.style === 'primary' ? 'selected' : ''}>Синий</option><option value="success" ${b.style === 'success' ? 'selected' : ''}>Зелёный</option><option value="danger" ${b.style === 'danger' ? 'selected' : ''}>Красный</option></select></label></div><label class="bot-visible"><input type="checkbox" data-bot-visible ${b.visible ? 'checked' : ''}> Показывать</label></div>`).join('');
  $('botLabelRows').innerHTML = botContent.labels.map(item => `<div class="bot-small-label" data-key="${escapeHtml(item.key)}"><strong>${escapeHtml(item.title)}</strong><label>Подпись<input data-ui-label maxlength="64" value="${escapeHtml(item.label)}"></label><label>Custom emoji ID<input data-ui-icon inputmode="numeric" maxlength="30" placeholder="Числовой ID" value="${escapeHtml(item.icon_custom_emoji_id || '')}"></label><label>Цвет<select data-ui-style class="control-input"><option value="" ${!item.style ? 'selected' : ''}>Стандартный</option><option value="primary" ${item.style === 'primary' ? 'selected' : ''}>Синий</option><option value="success" ${item.style === 'success' ? 'selected' : ''}>Зелёный</option><option value="danger" ${item.style === 'danger' ? 'selected' : ''}>Красный</option></select></label><small>${item.variables.length ? 'Переменные: ' + item.variables.map(v => '{'+escapeHtml(v)+'}').join(', ') : 'Обычная подпись'}</small></div>`).join('');
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
      label_appearances:Object.fromEntries(botContent.labels.map(item => [item.key,{icon_custom_emoji_id:item.icon_custom_emoji_id || null,style:item.style || null}])),
      buttons:botContent.buttons.map(b => ({action:b.action,label:b.label,position:b.position,visible:b.visible,icon_custom_emoji_id:b.icon_custom_emoji_id || null,style:b.style || null}))
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
$('ticketSearch').addEventListener('input', renderTickets);
document.querySelectorAll('[data-ticket-filter]').forEach(node => node.addEventListener('click', () => { ticketFilter=node.dataset.ticketFilter; renderTickets(); }));
$('activityCategory').addEventListener('change', async () => { activityPage=1; try { activityData=await request(`/api/activity?category=${encodeURIComponent($('activityCategory').value)}&page=1`); renderEvents(); } catch(error) { toast(error.message); } });
for (const [id,delta] of [['activityPrev',-1],['activityNext',1]]) $(id).addEventListener('click', async () => { activityPage+=delta; try { activityData=await request(`/api/activity?category=${encodeURIComponent($('activityCategory').value)}&page=${activityPage}`); renderEvents(); } catch(error) { activityPage-=delta; toast(error.message); } });
$('botButtonRows').addEventListener('click', event => { const move=event.target.closest('[data-move]'); if(move) moveBotButton(move.closest('[data-action]').dataset.action, move.dataset.move); });
$('botButtonRows').addEventListener('input', event => { const row=event.target.closest('[data-action]'); if(!row) return; const button=botContent.buttons.find(b => b.action === row.dataset.action); if(event.target.matches('[data-bot-label]')) button.label=event.target.value; if(event.target.matches('[data-bot-icon]')) button.icon_custom_emoji_id=event.target.value.trim(); if(event.target.matches('[data-bot-style]')) button.style=event.target.value; if(event.target.matches('[data-bot-visible]')) button.visible=event.target.checked; renderBotPreview(); });
$('botButtonRows').addEventListener('change', event => { const row=event.target.closest('[data-action]'); if(!row) return; const button=botContent.buttons.find(b => b.action === row.dataset.action); if(event.target.matches('[data-bot-style]')) button.style=event.target.value; if(event.target.matches('[data-bot-visible]')) button.visible=event.target.checked; renderBotPreview(); });
$('botLabelRows').addEventListener('input', event => { const row=event.target.closest('[data-key]'); if(!row) return; const item=botContent.labels.find(item => item.key === row.dataset.key); if(event.target.matches('[data-ui-label]')) item.label=event.target.value; if(event.target.matches('[data-ui-icon]')) item.icon_custom_emoji_id=event.target.value.trim(); if(event.target.matches('[data-ui-style]')) item.style=event.target.value; });
$('botLabelRows').addEventListener('change', event => { const row=event.target.closest('[data-key]'); if(row && event.target.matches('[data-ui-style]')) botContent.labels.find(item => item.key === row.dataset.key).style=event.target.value; });
$('botTextRows').addEventListener('input', event => { const row=event.target.closest('[data-key]'); if(!row) return; botContent.texts.find(t => t.key === row.dataset.key).body=event.target.value; renderBotPreview(); });
$('botSystemTextRows').addEventListener('input', event => { const row=event.target.closest('[data-key]'); if(row) botContent.texts.find(t => t.key === row.dataset.key).body=event.target.value; });
$('saveBotContent').addEventListener('click', saveBotContent);
$('resetBotContent').addEventListener('click', () => { if(!botContent) return; botContent.texts.forEach(t => t.body=t.default); botContent.buttons.forEach((b,index) => { b.label=b.default; b.position=index; b.visible=true; b.icon_custom_emoji_id=null; b.style=null; }); botContent.labels.forEach(item => {item.label=item.default;item.icon_custom_emoji_id=null;item.style=null;}); renderBotContent(); toast('Исходные значения подготовлены. Нажмите «Сохранить изменения», чтобы применить их.'); });
document.addEventListener('keydown', event => { if(event.key === 'Escape') closeDrawer(); });
(async () => {
  try { const config = await request('/api/public-config'); $('brandName').textContent = config.app_name; document.title = `${config.app_name} — управление сервисом`; } catch {}
  try { const result=await request('/api/me'); csrf=result.csrf_token; $('adminId').textContent=`Администратор · ${result.admin_id}`; showApp(); await loadData(); goto('overview'); } catch { showLogin(); }
})();

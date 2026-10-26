const $ = (id) => document.getElementById(id);
let csrf = '';
let tariffs = [];
let squads = [];
let overview = null;
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
  [overview, tariffs, squads] = await Promise.all([request('/api/overview'), request('/api/tariffs'), request('/api/squads')]);
  renderOverview(); renderTariffs(); renderSquads(); renderEvents();
}
function goto(view) {
  activeView = view;
  const labels = {overview:['Обзор','Добро пожаловать','Управляйте тарифами и доступом из одного пространства.'], tariffs:['Тарифы','Тарифы','Создавайте тарифы, настраивайте цены и выбирайте сквады.'], squads:['Сквады','Сквады Remnawave','Назначайте доступные внутренние сквады вашим тарифам.'], activity:['Журнал','История изменений','Каждое действие администратора сохраняется.']};
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
document.addEventListener('keydown', event => { if(event.key === 'Escape') closeDrawer(); });
(async () => {
  try { const config = await request('/api/public-config'); $('brandName').textContent = config.app_name; document.title = `${config.app_name} — управление сервисом`; } catch {}
  try { const result=await request('/api/me'); csrf=result.csrf_token; $('adminId').textContent=`Администратор · ${result.admin_id}`; showApp(); await loadData(); goto('overview'); } catch { showLogin(); }
})();

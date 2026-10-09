const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const count = value => new Intl.NumberFormat('zh-TW').format(value);
const finite = value => typeof value === 'number' && Number.isFinite(value);
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const state = { maps: [], filtered: [], query: '', region: '', selected: new Map(), controllers: new Map(), dialog: null };
let cardObserver;
let imageObserver;
let toastTimer;
let dialogTrigger;
let controllerId = 0;

const copyIcon = '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5" y="5" width="8" height="9" rx="1"/><path d="M10 5V2H2v9h3"/></svg>';
const rawCoordinate = chest => `X ${count(chest.x)} · Y ${count(chest.y)}`;
const chestNumber = (map, chest) => String(map.chests.indexOf(chest) + 1).padStart(2, '0');
const getSelection = map => map.chests.find(chest => String(chest.id) === String(state.selected.get(map.id))) ?? map.chests[0];

function announce(text) {
  $('#toast').textContent = text;
  $('#toast').classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 2600);
}

async function copyCoordinates(map) {
  const chest = getSelection(map);
  if (!chest) return;
  const text = `${map.name}（${map.id}） ${chest.name || '黃金寶箱'}：X ${chest.x}，Y ${chest.y}`;
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
    else {
      const input = document.createElement('textarea');
      input.value = text;
      input.style.cssText = 'position:fixed;left:-10000px;top:0';
      document.body.append(input);
      input.select();
      const copied = document.execCommand('copy');
      input.remove();
      if (!copied) throw new Error('clipboard unavailable');
    }
    announce(`已複製 ${map.name} · ${chest.x}, ${chest.y}`);
  } catch {
    announce(`座標：${chest.x}, ${chest.y}（無法自動複製）`);
  }
}

function selectedDetail(map) {
  const chest = getSelection(map);
  if (!chest) return '';
  const warnings = [chest.hasInteraction === false ? '未配置開箱事件' : null, chest.note, chest.interactionNote, chest.interaction_note].filter(value => typeof value === 'string' && value);
  return `<p><span class="selected-coordinate">${escape(rawCoordinate(chest))}</span><br>NPC ${escape(chest.npcId)}${chest.slot != null ? ` · 場景序號 ${escape(chest.slot)}` : ''}${warnings.length ? `<br>${warnings.map(escape).join('；')}` : ''}</p><button class="copy-button" data-action="copy">${copyIcon}複製座標</button>`;
}

function locations(map) {
  const selected = getSelection(map);
  return `<aside class="locations" aria-label="${escape(map.name)}寶箱座標"><div class="locations-title">寶箱座標 <span>${map.chests.length} 個位置</span></div><ol class="chest-list">${map.chests.map(chest => `<li><button class="chest-location" data-chest="${escape(chest.id)}" aria-pressed="${chest === selected}" aria-label="${escape(chest.name)} ${escape(rawCoordinate(chest))}"><span class="location-number">${chestNumber(map, chest)}</span><span class="location-text"><strong>${escape(chest.name || '黃金寶箱')}</strong><small>${escape(rawCoordinate(chest))}</small></span><span class="location-arrow" aria-hidden="true">↗</span></button></li>`).join('')}</ol><div class="selected-detail">${selectedDetail(map)}</div></aside>`;
}

function mapBody(map, expanded = false) {
  const imageKind = map.minimap.type === 'bitmap' ? '遊戲小地圖' : '導航資料地圖';
  return `<div class="card-body"><div class="map-column"><div class="minimap-wrap"><span class="map-badge ${map.minimap.type === 'bitmap' ? '' : 'navigation'}">${imageKind}</span><div class="map-controls" role="group" aria-label="${escape(map.name)}地圖操作"><button data-action="out" aria-label="縮小地圖">−</button><button data-action="in" aria-label="放大地圖">＋</button><button data-action="reset">重設</button>${expanded ? '' : '<button data-action="expand" aria-label="開啟放大地圖">⛶</button>'}</div><svg class="minimap" xmlns="http://www.w3.org/2000/svg" tabindex="0" role="group" aria-label="${escape(map.name)}互動小地圖，${map.chests.length}個寶箱。方向鍵平移，加減鍵縮放，Home重設。"></svg></div><div class="map-bottom"><span class="map-readout"><i class="marker-example"></i><span>寶箱位置 · 點擊空白處查看座標</span></span><span class="zoom-level">100%</span></div></div>${locations(map)}</div>`;
}

function mapCard(map, index) {
  return `<article class="map-card" id="map-${map.id}" data-map="${map.id}" aria-labelledby="title-${map.id}"><header class="card-heading"><div class="map-title"><span class="map-order" aria-hidden="true">${String(index + 1).padStart(2, '0')}</span><div><h3 id="title-${map.id}">${escape(map.name)}</h3><p>${escape(map.region || '其他地區')}<span>／</span>場景 ${map.id}</p></div></div><span class="card-chest-count"><b>${map.chests.length}</b> 個寶箱</span></header>${mapBody(map)}</article>`;
}

function setActiveMap(id) {
  $$('.index-map').forEach(button => {
    const active = Number(button.dataset.map) === Number(id);
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'location');
    else button.removeAttribute('aria-current');
  });
}

function renderResults() {
  cardObserver?.disconnect();
  imageObserver?.disconnect();
  for (const controller of state.controllers.values()) controller.dispose();
  state.controllers.clear();
  const query = state.query.trim().toLocaleLowerCase();
  state.filtered = state.maps.filter(map => (!state.region || map.region === state.region) && (!query || `${map.name} ${map.region} ${map.id} ${map.chests.map(chest => `${chest.name} ${chest.x} ${chest.y}`).join(' ')}`.toLocaleLowerCase().includes(query)));
  const chests = state.filtered.reduce((sum, map) => sum + map.chests.length, 0);
  $('#results-count').textContent = `${count(state.filtered.length)} 張地圖 · ${count(chests)} 個寶箱`;
  $('#index-count').textContent = `${state.filtered.length} / ${state.maps.length}`;
  $('#map-index').innerHTML = state.filtered.map(map => `<button class="index-map" data-map="${map.id}" aria-label="${escape(map.name)}，場景 ${map.id}，${map.chests.length} 個寶箱"><span>${escape(map.name)}<small>${escape(map.region || '其他地區')} · ${map.id}</small></span><span class="index-count">${map.chests.length}</span></button>`).join('');
  $('#map-list').innerHTML = state.filtered.length ? state.filtered.map(mapCard).join('') : '<div class="empty-state"><h3>沒有符合的地圖</h3><p>試試其他地圖名稱、地區或場景編號。</p><button class="clear-button" data-action="clear">清除篩選</button></div>';
  imageObserver = new IntersectionObserver(entries => entries.forEach(entry => {
    if (!entry.isIntersecting) return;
    state.controllers.get(Number(entry.target.dataset.map))?.load();
    imageObserver.unobserve(entry.target);
  }), { rootMargin: '850px 0px' });
  cardObserver = new IntersectionObserver(entries => {
    const candidates = entries.filter(entry => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
    if (candidates.length) setActiveMap(candidates[0].target.dataset.map);
  }, { rootMargin: '-6% 0px -60% 0px', threshold: 0 });
  for (const map of state.filtered) {
    const root = document.getElementById(`map-${map.id}`);
    const controller = new Minimap(root, map);
    state.controllers.set(map.id, controller);
    imageObserver.observe(root);
    cardObserver.observe(root);
  }
  if (state.filtered.length) setActiveMap(state.filtered[0].id);
}

function selectChest(map, id, focusMap = false) {
  const chest = map.chests.find(row => String(row.id) === String(id));
  if (!chest) return;
  state.selected.set(map.id, chest.id);
  const controllers = [state.controllers.get(map.id), state.dialog?.map.id === map.id ? state.dialog : null].filter(Boolean);
  for (const controller of controllers) {
    controller.setSelection();
    if (focusMap) controller.centerOn(chest);
  }
}

function scrollToMap(id, updateHash = true) {
  const targetMap = state.maps.find(map => map.id === Number(id));
  if (!targetMap) return;
  if (!state.filtered.some(map => map.id === targetMap.id)) {
    state.query = '';
    state.region = '';
    $('#search').value = '';
    $('#region').value = '';
    renderResults();
  }
  const target = document.getElementById(`map-${targetMap.id}`);
  target?.scrollIntoView({ behavior: reducedMotion ? 'instant' : 'smooth', block: 'start' });
  setActiveMap(targetMap.id);
  state.controllers.get(targetMap.id)?.load();
  if (updateHash) history.replaceState(null, '', `#map-${targetMap.id}`);
}

function openMap(map, trigger) {
  dialogTrigger = trigger;
  const dialog = $('#map-dialog');
  $('#dialog-title').textContent = `${map.name} · ${map.id}`;
  $('#dialog-content').innerHTML = mapBody(map, true);
  state.dialog?.dispose();
  dialog.showModal();
  state.dialog = new Minimap($('#dialog-content'), map);
  state.dialog.load();
  document.body.style.overflow = 'hidden';
  $('.close-dialog').focus();
}

function closeMap() {
  $('#map-dialog').close();
}

class Minimap {
  constructor(root, map) {
    this.root = root;
    this.map = map;
    this.svg = $('.minimap', root);
    this.width = map.minimap.width;
    this.height = map.minimap.height;
    this.projection = map.minimap.projection;
    this.instanceId = ++controllerId;
    const positions = map.chests.map(chest => this.project(chest));
    const left = Math.min(0, ...positions.map(point => point.x - 30));
    const top = Math.min(0, ...positions.map(point => point.y - 30));
    const right = Math.max(this.width, ...positions.map(point => point.x + 30));
    const bottom = Math.max(this.height, ...positions.map(point => point.y + 30));
    this.base = { x: left, y: top, w: right - left, h: bottom - top };
    this.view = { ...this.base };
    this.drag = null;
    this.loaded = false;
    this.abort = new AbortController();
    const { signal } = this.abort;
    const gridId = `grid-${this.instanceId}`;
    this.svg.innerHTML = `<defs><pattern id="${gridId}" width="64" height="64" patternUnits="userSpaceOnUse"><path d="M64 0H0V64" fill="none" stroke="#91aaa7" stroke-opacity=".045"/></pattern></defs><rect x="-100000" y="-100000" width="200000" height="200000" fill="#10151b"/><rect x="-100000" y="-100000" width="200000" height="200000" fill="url(#${gridId})"/><image x="0" y="0" width="${this.width}" height="${this.height}" preserveAspectRatio="none"/><g class="map-crosshair" hidden></g>${map.chests.map((chest, index) => `<g class="marker" data-chest="${escape(chest.id)}" role="button" tabindex="0" aria-pressed="false" aria-label="${escape(map.name)}，${escape(chest.name || '黃金寶箱')} ${index + 1}，${escape(rawCoordinate(chest))}"><title>${escape(chest.name || '黃金寶箱')} ${index + 1} · ${escape(rawCoordinate(chest))}</title><g class="marker-art"><circle class="marker-selection" r="17"/><circle class="marker-halo" r="12"/><circle class="marker-core" r="6.5"/><text class="marker-number" x="11" y="-10">${String(index + 1).padStart(2, '0')}</text></g></g>`).join('')}`;
    this.image = $('image', this.svg);
    this.image.addEventListener('error', () => {
      $('.map-badge', this.root).textContent = '圖像載入失敗';
      $('.map-readout', this.root).innerHTML = '<span>無法讀取底圖；寶箱座標仍保留</span>';
    }, { signal });
    this.svg.addEventListener('pointerdown', event => this.pointerDown(event), { signal });
    this.svg.addEventListener('pointermove', event => this.pointerMove(event), { signal });
    this.svg.addEventListener('pointerup', event => this.pointerUp(event), { signal });
    this.svg.addEventListener('pointercancel', () => this.endDrag(), { signal });
    this.svg.addEventListener('lostpointercapture', () => this.endDrag(), { signal });
    this.svg.addEventListener('keydown', event => this.keyDown(event), { signal });
    this.svg.addEventListener('wheel', event => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      this.zoom(event.deltaY < 0 ? 1.18 : 1 / 1.18, this.pointerPosition(event));
    }, { passive: false, signal });
    this.svg.addEventListener('click', event => {
      const marker = event.target.closest('.marker');
      if (marker) selectChest(this.map, marker.dataset.chest);
    }, { signal });
    root.addEventListener('click', event => this.action(event), { signal });
    this.resizeObserver = new ResizeObserver(() => this.update());
    this.resizeObserver.observe(this.svg);
    this.update();
    this.setSelection();
  }

  project(chest) {
    return { x: chest.x * this.projection.scale_x + this.projection.offset_x, y: chest.y * this.projection.scale_y + this.projection.offset_y };
  }

  load() {
    if (this.loaded) return;
    this.loaded = true;
    this.image.setAttribute('href', this.map.minimap.image);
  }

  update() {
    this.svg.setAttribute('viewBox', `${this.view.x} ${this.view.y} ${this.view.w} ${this.view.h}`);
    const bounds = this.svg.getBoundingClientRect();
    const scale = Math.max(this.view.w / (bounds.width || 1), this.view.h / (bounds.height || 1));
    $$('.marker', this.svg).forEach((marker, index) => {
      const point = this.project(this.map.chests[index]);
      marker.setAttribute('transform', `translate(${point.x} ${point.y})`);
      $('.marker-art', marker).setAttribute('transform', `scale(${scale})`);
    });
    if (this.crosshair) {
      const crosshair = $('.map-crosshair', this.svg);
      crosshair.setAttribute('transform', `translate(${this.crosshair.x} ${this.crosshair.y}) scale(${scale})`);
    }
    $('.zoom-level', this.root).textContent = `${Math.round(this.base.w / this.view.w * 100)}%`;
    $('[data-action="in"]', this.root).disabled = this.base.w / this.view.w >= 7.99;
    $('[data-action="out"]', this.root).disabled = this.base.w / this.view.w <= 1.001;
  }

  setSelection() {
    const selection = getSelection(this.map);
    $$('[data-chest]', this.root).forEach(element => {
      const selected = String(element.dataset.chest) === String(selection?.id);
      element.setAttribute('aria-pressed', String(selected));
      element.classList.toggle('selected', selected);
    });
    $('.selected-detail', this.root).innerHTML = selectedDetail(this.map);
  }

  centerOn(chest) {
    const point = this.project(chest);
    if (this.view.w === this.base.w && this.view.h === this.base.h) return;
    this.view.x = point.x - this.view.w / 2;
    this.view.y = point.y - this.view.h / 2;
    this.limitPan();
    this.update();
  }

  action(event) {
    const button = event.target.closest('button');
    if (!button || button.disabled) return;
    if (button.dataset.chest) {
      selectChest(this.map, button.dataset.chest, true);
      return;
    }
    switch (button.dataset.action) {
      case 'in': this.zoom(1.5); break;
      case 'out': this.zoom(1 / 1.5); break;
      case 'reset': this.reset(); break;
      case 'copy': copyCoordinates(this.map); break;
      case 'expand': openMap(this.map, button); break;
    }
  }

  zoom(factor, anchor = { x: this.view.x + this.view.w / 2, y: this.view.y + this.view.h / 2 }) {
    const newZoom = Math.max(1, Math.min(8, this.base.w / this.view.w * factor));
    const width = this.base.w / newZoom;
    const height = this.base.h / newZoom;
    const xRatio = (anchor.x - this.view.x) / this.view.w;
    const yRatio = (anchor.y - this.view.y) / this.view.h;
    this.view = { x: anchor.x - width * xRatio, y: anchor.y - height * yRatio, w: width, h: height };
    this.limitPan();
    this.update();
  }

  reset() {
    this.view = { ...this.base };
    this.crosshair = null;
    $('.map-crosshair', this.svg).replaceChildren();
    $('.map-readout', this.root).innerHTML = '<i class="marker-example"></i><span>寶箱位置 · 點擊空白處查看座標</span>';
    this.update();
  }

  limitPan() {
    const marginX = this.base.w * .08;
    const marginY = this.base.h * .08;
    this.view.x = Math.max(this.base.x - marginX, Math.min(this.base.x + this.base.w - this.view.w + marginX, this.view.x));
    this.view.y = Math.max(this.base.y - marginY, Math.min(this.base.y + this.base.h - this.view.h + marginY, this.view.y));
  }

  pointerPosition(event) {
    const matrix = this.svg.getScreenCTM();
    if (!matrix) return { x: 0, y: 0 };
    return new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
  }

  pointerDown(event) {
    if (event.button !== 0 || event.target.closest('.marker')) return;
    const matrix = this.svg.getScreenCTM();
    if (!matrix) return;
    this.drag = { id: event.pointerId, clientX: event.clientX, clientY: event.clientY, x: this.view.x, y: this.view.y, scaleX: matrix.a, scaleY: matrix.d, moved: false };
    this.svg.setPointerCapture(event.pointerId);
    this.svg.classList.add('dragging');
  }

  pointerMove(event) {
    if (!this.drag || this.drag.id !== event.pointerId) return;
    const dx = event.clientX - this.drag.clientX;
    const dy = event.clientY - this.drag.clientY;
    if (Math.hypot(dx, dy) > 4) this.drag.moved = true;
    if (!this.drag.moved) return;
    this.view.x = this.drag.x - dx / this.drag.scaleX;
    this.view.y = this.drag.y - dy / this.drag.scaleY;
    this.limitPan();
    this.update();
  }

  pointerUp(event) {
    if (!this.drag || this.drag.id !== event.pointerId) return;
    if (!this.drag.moved) this.inspectPoint(this.pointerPosition(event));
    this.endDrag();
    if (this.svg.hasPointerCapture(event.pointerId)) this.svg.releasePointerCapture(event.pointerId);
  }

  endDrag() {
    this.drag = null;
    this.svg.classList.remove('dragging');
  }

  inspectPoint(point) {
    const x = Math.round((point.x - this.projection.offset_x) / this.projection.scale_x);
    const y = Math.round((point.y - this.projection.offset_y) / this.projection.scale_y);
    this.crosshair = { x: point.x, y: point.y };
    const crosshair = $('.map-crosshair', this.svg);
    crosshair.removeAttribute('hidden');
    crosshair.innerHTML = '<path class="crosshair" d="M-10 0H-3M3 0H10M0-10V-3M0 3V10"/><circle class="crosshair" r="3"/>';
    $('.map-readout', this.root).innerHTML = `<span class="coordinate-readout">點選位置：X ${count(x)} · Y ${count(y)}</span>`;
    this.update();
  }

  keyDown(event) {
    const marker = event.target.closest('.marker');
    if (marker && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      selectChest(this.map, marker.dataset.chest);
      return;
    }
    const distanceX = this.view.w * .1;
    const distanceY = this.view.h * .1;
    switch (event.key) {
      case '+': case '=': this.zoom(1.5); break;
      case '-': case '_': this.zoom(1 / 1.5); break;
      case 'Home': this.reset(); break;
      case 'ArrowLeft': this.view.x -= distanceX; break;
      case 'ArrowRight': this.view.x += distanceX; break;
      case 'ArrowUp': this.view.y -= distanceY; break;
      case 'ArrowDown': this.view.y += distanceY; break;
      default: return;
    }
    event.preventDefault();
    this.limitPan();
    this.update();
  }

  dispose() {
    this.abort.abort();
    this.resizeObserver.disconnect();
  }
}

function validateAndJoin(roster, atlas) {
  if (!Array.isArray(roster.maps) || !Array.isArray(atlas.maps)) throw new Error('地圖資料格式錯誤');
  const release = roster.releaseId ?? roster.release_id;
  const atlasRelease = atlas.release_id ?? atlas.releaseId;
  if (release && atlasRelease && release !== atlasRelease) throw new Error('寶箱與地圖資料版本不一致');
  const atlasMaps = new Map(atlas.maps.map(map => [Number(map.id), map]));
  const seen = new Set();
  return roster.maps.map(map => {
    const id = Number(map.id);
    const atlasMap = atlasMaps.get(id);
    const minimap = atlasMap?.minimap;
    if (!Number.isInteger(id) || seen.has(id)) throw new Error('場景編號重複或無效');
    seen.add(id);
    if (!minimap || !['bitmap', 'navigation'].includes(minimap.type) || !finite(minimap.width) || !finite(minimap.height) || minimap.width <= 0 || minimap.height <= 0) throw new Error(`場景 ${id} 缺少經驗證的地圖`);
    if (typeof minimap.image !== 'string' || !/^assets\/maps\/[\w.-]+\.(png|webp|svg)$/i.test(minimap.image)) throw new Error(`場景 ${id} 圖像路徑無效`);
    const projection = minimap.projection;
    if (!projection || !['scale_x', 'scale_y', 'offset_x', 'offset_y'].every(key => finite(projection[key])) || projection.scale_x === 0 || projection.scale_y === 0) throw new Error(`場景 ${id} 缺少座標投影`);
    if (!Array.isArray(map.chests) || !map.chests.length || map.chests.some(chest => !finite(chest.x) || !finite(chest.y))) throw new Error(`場景 ${id} 寶箱座標無效`);
    return { ...map, id, minimap, name: String(map.name || atlasMap.name || `場景 ${id}`), region: String(map.region || '其他地區') };
  });
}

async function start() {
  try {
    const [roster, atlas] = await Promise.all(['./data/chests.json', './data/maps.json'].map(async path => {
      const response = await fetch(path);
      if (!response.ok) throw new Error(`無法讀取 ${path}（${response.status}）`);
      return response.json();
    }));
    state.maps = validateAndJoin(roster, atlas);
    const total = state.maps.reduce((sum, map) => sum + map.chests.length, 0);
    const bitmapCount = state.maps.filter(map => map.minimap.type === 'bitmap').length;
    const navCount = state.maps.length - bitmapCount;
    $('#total-chests').textContent = count(total);
    $('#total-maps').textContent = count(state.maps.length);
    $('#total-bitmaps').textContent = count(bitmapCount);
    $('#bitmap-caption').textContent = navCount ? `另 ${navCount} 張導航資料地圖` : '全數使用原始圖像';
    $('#sidebar-count').textContent = `${state.maps.length} MAPS`;
    $('#version').textContent = roster.clientVersion || roster.version || (roster.releaseId || '').split('_')[0] || 'WLRE';
    $('#source-note').textContent = `${typeof roster.sourceNote === 'string' ? roster.sourceNote : '遊戲名稱：黃金寶箱。位置取自遊戲場景資料，地圖優先採用遊戲原始小地圖。'} NPC 19117。`;
    $('#source-meta').textContent = `資料版本 ${roster.releaseId || roster.release_id || '—'}${roster.coverage?.eveScenes ? ` · 已檢索 ${count(roster.coverage.eveScenes)} 個場景` : ''}`;
    const regions = [...new Set(state.maps.map(map => map.region))].sort((a, b) => a.localeCompare(b, 'zh-Hant', { numeric: true }));
    $('#region').innerHTML = '<option value="">全部分區</option>' + regions.map(region => `<option value="${escape(region)}">${escape(region)} · ${state.maps.filter(map => map.region === region).length}</option>`).join('');
    renderResults();
    const initialMap = location.hash.match(/^#map-(\d+)$/)?.[1];
    if (initialMap) requestAnimationFrame(() => scrollToMap(initialMap, false));
  } catch (error) {
    $('#results-count').textContent = '資料尚未載入';
    $('#map-list').innerHTML = `<div class="empty-state"><h3>暫時無法展開地圖</h3><p>${escape(error.message)}</p><p>請重新整理頁面後再試一次。</p><button class="clear-button" data-action="reload">重新載入</button></div>`;
    console.error(error);
  }
}

$('#search').addEventListener('input', event => {
  state.query = event.target.value;
  renderResults();
  window.scrollTo({ top: 0, behavior: 'instant' });
});
$('#region').addEventListener('change', event => {
  state.region = event.target.value;
  renderResults();
  window.scrollTo({ top: 0, behavior: 'instant' });
});
$('#map-index').addEventListener('click', event => {
  const button = event.target.closest('[data-map]');
  if (button) scrollToMap(button.dataset.map);
});
$('#map-list').addEventListener('click', event => {
  const action = event.target.closest('[data-action]')?.dataset.action;
  if (action === 'clear') {
    state.query = '';
    state.region = '';
    $('#search').value = '';
    $('#region').value = '';
    renderResults();
    $('#search').focus();
  } else if (action === 'reload') location.reload();
});
$('.close-dialog').addEventListener('click', closeMap);
$('#map-dialog').addEventListener('click', event => {
  if (event.target !== $('#map-dialog')) return;
  const bounds = $('#map-dialog').getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) closeMap();
});
$('#map-dialog').addEventListener('close', () => {
  state.dialog?.dispose();
  state.dialog = null;
  document.body.style.overflow = '';
  dialogTrigger?.focus({ preventScroll: true });
});
window.addEventListener('hashchange', () => {
  const id = location.hash.match(/^#map-(\d+)$/)?.[1];
  if (id) scrollToMap(id, false);
});
document.addEventListener('keydown', event => {
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !$('#map-dialog').open && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) {
    event.preventDefault();
    $('#search').focus();
  }
});

start();

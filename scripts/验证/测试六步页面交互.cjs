// 模拟单步阅读、复制与失败回退；不启动浏览器，不代表视觉或真实剪贴板验收。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const page = fs.readFileSync(path.resolve(__dirname, '../../docs/使用指南/场景迁移与优化-六步指南.html'), 'utf8');
const script = page.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
function element(properties = {}) {
  return { textContent: '', disabled: true, hidden: false, open: false, handlers: {}, attrs: {},
    addEventListener(name, callback) { this.handlers[name] = callback; },
    setAttribute(name, value) { this.attrs[name] = value; },
    removeAttribute(name) { delete this.attrs[name]; },
    focus() { this.focused = true; },
    ...properties };
}
function create(options = {}) {
  const ids = { previous: element(), next: element(), 'view-position': element() };
  const panels = [], navigation = [], copies = [], writes = [], hashHandlers = {};
  const location = { hash: options.hash || '' };
  for (let i = 1; i <= 6; i++) {
    const textarea = element({ value: '', select() { this.selected = true; } });
    const fallback = element({ hidden: true, textarea, querySelector: () => textarea });
    const feedback = element();
    const details = [element(), element()];
    const panel = element({ id: 'step-' + i, hidden: i !== 1, feedback, fallback, details,
      querySelectorAll: () => details,
      querySelector: selector => selector === '.feedback' ? feedback : fallback });
    panels.push(panel);
    ids['step-' + i] = panel;
    ids['heading-' + i] = element();
    navigation.push(element({ dataset: { go: String(i) } }));
    const names = ['prompt-' + i, ...([4, 6].includes(i) ? ['changes-' + i] : [])];
    for (const name of names) {
      ids[name] = element({ textContent: '测试话术:' + name });
      copies.push(element({ dataset: { copy: name }, closest: () => panel }));
    }
  }
  const document = { getElementById: id => ids[id],
    querySelectorAll: selector => ({ '.step': panels, 'button[data-go]': navigation, 'button[data-copy]': copies })[selector] };
  const navigator = options.noClipboard ? {} : { clipboard: { async writeText(value) {
    if (options.clipboardBlocked) throw new Error('剪贴板受限'); writes.push(value);
  } } };
  vm.runInNewContext(script, { document, navigator, location, window: { addEventListener: (name, fn) => { hashHandlers[name] = fn; } } });
  return { ids, panels, navigation, copies, writes, location, hashHandlers };
}
function only(state, index) {
  assert.deepEqual(state.panels.filter(panel => !panel.hidden).map(panel => panel.id), ['step-' + index]);
}
(async () => {
  let checks = 0, state = create();
  only(state, 1); assert(state.ids.previous.disabled); assert(!state.ids.next.disabled); checks++;
  state.ids.next.handlers.click(); only(state, 2); assert(state.ids['heading-2'].focused); checks++;
  state.ids.previous.handlers.click(); only(state, 1); checks++;
  state.navigation[3].handlers.click(); only(state, 4); assert.equal(state.navigation[3].attrs['aria-current'], 'step'); checks++;
  state.panels[3].details[0].open = true; state.ids.next.handlers.click();
  only(state, 5); assert.equal(state.panels[3].details[0].open, false); checks++;
  state.navigation[5].handlers.click(); only(state, 6); assert(state.ids.next.disabled); checks++;
  state = create({ hash: '#step-5' }); only(state, 5); checks++;
  state = create({ hash: '#unknown' }); only(state, 1); checks++;
  state.location.hash = '#step-3'; state.hashHandlers.hashchange(); only(state, 3); checks++;
  state = create(); await state.copies[0].handlers.click();
  assert.deepEqual(state.writes, ['测试话术:prompt-1']); assert(state.panels[0].feedback.textContent.includes('已复制')); only(state, 1); checks++;
  state = create({ clipboardBlocked: true });
  await state.copies.find(button => button.dataset.copy === 'changes-4').handlers.click();
  assert.equal(state.panels[3].fallback.hidden, false); assert(state.panels[3].fallback.textarea.selected);
  assert.equal(state.panels[3].fallback.textarea.value, '测试话术:changes-4'); checks++;
  state = create({ noClipboard: true }); await state.copies[0].handlers.click();
  assert(state.panels[0].feedback.textContent.includes('手动复制')); checks++;
  state = create(); await state.copies.find(button => button.dataset.copy === 'prompt-6').handlers.click();
  await state.copies.find(button => button.dataset.copy === 'changes-6').handlers.click();
  assert.deepEqual(state.writes, ['测试话术:prompt-6', '测试话术:changes-6']); only(state, 1); checks++;
  assert(!script.includes('localStorage')); assert.equal(state.copies.length, 8); checks++;
  console.log(checks + '项模拟交互检查通过；未使用真实浏览器或剪贴板。');
})().catch(error => { console.error(error); process.exitCode = 1; });

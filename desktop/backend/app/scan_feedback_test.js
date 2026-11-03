// node desktop/backend/app/scan_feedback_test.js
// 4.1.8：有待确认数量时扫码枪的条码打进数量框要当作下一次扫码；扫码超收要有提示文字。
'use strict';
global.window = {};
require('./web/scan-feedback.js');
const F = window.ScanFeedback, assert = require('assert');
const fast = n => Array(n).fill(12), slow = n => Array(n).fill(260);

// 扫码枪：13 位 69 码、带字母的商品编码、快速输入的短条码
assert.strictEqual(F.looksLikeScan('6936333312036', fast(12)), true);
assert.strictEqual(F.looksLikeScan('6936333312036', slow(12)), true, '超过数量上限位数的一定不是数量');
assert.strictEqual(F.looksLikeScan('H2298', slow(4)), true, '含字母的不是数量');
assert.strictEqual(F.looksLikeScan('12345678', fast(7)), true);
// 人手改数量：慢速输入的数字
assert.strictEqual(F.looksLikeScan('12', [180]), false);
assert.strictEqual(F.looksLikeScan('240', slow(2)), false);
assert.strictEqual(F.looksLikeScan('1000', slow(3)), false);
assert.strictEqual(F.looksLikeScan('', []), false);
// 只有 3 位：即使很快也按数量处理（数量框最常见的就是三位以内）
assert.strictEqual(F.looksLikeScan('120', fast(2)), false);

assert.strictEqual(F.overText(null), '');
const t = F.overText({sku_id: 'H2298', name: 'AKF散粉新款4#', plan: 240, recv: 241, over: 1});
assert.ok(t.indexOf('超收') === 0 && t.indexOf('AKF散粉新款4#') > 0 && t.indexOf('241') > 0 && t.indexOf('240') > 0 && /多 1$/.test(t), t);
console.log('scan-feedback ok');

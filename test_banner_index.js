const assert = require('node:assert/strict');
const { wrap } = require('./static/banner-index.js');
assert.equal(wrap(-1, 3), 2);
assert.equal(wrap(0, 3), 0);
assert.equal(wrap(2, 3), 2);
assert.equal(wrap(3, 3), 0);
assert.equal(wrap(-4, 3), 2);
assert.equal(wrap(0, 0), 0);
console.log('ALL BANNER INDEX CHECKS PASSED');
